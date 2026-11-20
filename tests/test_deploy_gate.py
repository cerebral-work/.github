"""The deploy gate: one claims-check source, enforce only, and a deploy range
that starts at the last successful deployment.

python3 -m unittest discover -s tests -v
No mocks of the unit under test: the range step is the real `run:` block of
deploy-gate.yml, run by bash against a real git repo and a local HTTP server
that answers the two GitHub endpoints it reads.
"""
import http.server
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, urlparse

import yaml

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / '.github' / 'workflows' / 'deploy-gate.yml'
JURY = ROOT / '.github' / 'workflows' / 'agent-jury.yml'
GIT_ENV = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull}
IDENT = ['-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
         '-c', 'commit.gpgsign=false']


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], env=GIT_ENV, check=True,
                          capture_output=True, text=True).stdout.strip()


def load(path):
    # PyYAML reads the `on:` key as True.
    return yaml.safe_load(path.read_text())


def step(wf, job, prefix):
    return next(s for s in wf['jobs'][job]['steps'] if s.get('name', '').startswith(prefix))


class SingleSource(unittest.TestCase):
    """Both workflows run the same claims script; the logic lives in one file."""

    def test_both_workflows_run_scripts_claims_job_sh(self):
        jury = step(load(JURY), 'claims', 'Check claims')['run'].strip()
        gate = step(load(GATE), 'claims', 'Check claims')['run'].strip()
        for run in (jury, gate):
            self.assertEqual(run, 'bash -e .jury-tools/scripts/claims_job.sh')
        self.assertTrue((ROOT / 'scripts' / 'claims_job.sh').is_file())


class EnforceOnly(unittest.TestCase):

    def test_no_report_mode_in_the_gate(self):
        wf = load(GATE)
        self.assertNotIn('claims_mode', wf[True]['workflow_call']['inputs'])
        self.assertEqual(step(wf, 'claims', 'Check claims')['env']['CLAIMS_MODE'], 'enforce')

    def test_gate_reads_deployments_and_nothing_more(self):
        perms = load(GATE)['jobs']['claims']['permissions']
        self.assertEqual(perms, {'contents': 'read', 'deployments': 'read'})


class WorkflowCallBlock(unittest.TestCase):

    def test_no_expression_outside_output_values(self):
        block, bad = False, []
        for n, line in enumerate(GATE.read_text().splitlines(), 1):
            if line.strip() == 'workflow_call:':
                block = True
                continue
            if block and line.startswith('jobs:'):
                break
            if block and '${{' in line and not line.strip().startswith('value:'):
                bad.append('%d: %s' % (n, line.strip()))
        self.assertEqual(bad, [])


class FakeGitHub:
    """Answers GET /repos/o/r/deployments and /repos/o/r/deployments/<id>/statuses.
    deployments: {environment: [(id, sha, latest_state), ...newest first]}."""

    def __init__(self):
        self.deployments, self.status, self.seen = {}, 200, []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                url = urlparse(self.path)
                outer.seen.append((self.path, self.headers.get('Authorization')))
                if outer.status != 200:
                    self.send_response(outer.status)
                    self.end_headers()
                    return
                q = parse_qs(url.query)
                m = re.fullmatch(r'/repos/o/r/deployments/(\d+)/statuses', url.path)
                if url.path == '/repos/o/r/deployments':
                    rows = outer.deployments.get(q.get('environment', [''])[0], [])
                    per, page = int(q['per_page'][0]), int(q.get('page', ['1'])[0])
                    body = [{'id': i, 'sha': s} for i, s, _ in rows[(page - 1) * per:page * per]]
                elif m:
                    states = [st for i, _, st in sum(outer.deployments.values(), []) if i == int(m.group(1))]
                    body = [{'state': states[0]}] if states and states[0] else []
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = 'http://127.0.0.1:%d' % self.server.server_port


class Range(unittest.TestCase):
    """The real `Resolve the deploy range` step against a fake API and real git."""

    @classmethod
    def setUpClass(cls):
        cls.script = step(load(GATE), 'claims', 'Resolve the deploy range')['run']
        cls.api = FakeGitHub()
        cls.tmp = tempfile.TemporaryDirectory()
        root = cls.root = Path(cls.tmp.name) / 'repo'
        root.mkdir()
        git(root, 'init', '-q', '-b', 'main')
        cls.shas = []
        for n in range(3):
            (root / 'f.txt').write_text('%d\n' % n)
            git(root, *IDENT, 'add', '-A')
            git(root, *IDENT, 'commit', '-q', '-m', str(n))
            cls.shas.append(git(root, 'rev-parse', 'HEAD'))

    @classmethod
    def tearDownClass(cls):
        cls.api.server.shutdown()
        cls.tmp.cleanup()

    def run_range(self, environment='web-prod', base_sha='', deployments=None, status=200):
        self.api.deployments, self.api.status, self.api.seen = deployments or {}, status, []
        out = Path(self.tmp.name) / 'out'
        out.write_text('')
        env = {**GIT_ENV, 'API_URL': self.api.url, 'REPO': 'o/r', 'GATE_TOKEN': 'fixture',
               'ENVIRONMENT': environment, 'BASE_INPUT': base_sha,
               'GITHUB_OUTPUT': str(out), 'GITHUB_STEP_SUMMARY': os.devnull,
               'RUNNER_TEMP': self.tmp.name}
        proc = subprocess.run(['bash', '-e', '-c', self.script], cwd=self.root, env=env,
                              capture_output=True, text=True)
        outputs = dict(line.split('=', 1) for line in out.read_text().splitlines() if '=' in line)
        return proc.returncode, outputs, proc.stdout + proc.stderr

    def test_base_is_the_newest_successful_deployment(self):
        a, b, c = self.shas
        deps = {'web-prod': [(3, b, 'failure'), (2, a, 'success'), (1, a, 'inactive')],
                'web-sta': [(9, b, 'success')]}
        rc, out, log = self.run_range(deployments=deps)
        self.assertEqual(rc, 0, log)
        self.assertEqual((out.get('base'), out.get('sha')), (a, c))
        self.assertTrue(all(auth == 'Bearer fixture' for _, auth in self.api.seen))

    def test_no_successful_deployment_fails_closed_and_names_base_sha(self):
        rc, out, log = self.run_range(deployments={'web-prod': [(3, self.shas[1], 'failure')]})
        self.assertNotEqual(rc, 0)
        self.assertIn('base_sha', log)
        self.assertNotIn('base', out)

    def test_base_sha_input_is_used_without_the_api(self):
        rc, out, log = self.run_range(base_sha=self.shas[0], status=500)
        self.assertEqual(rc, 0, log)
        self.assertEqual(out.get('base'), self.shas[0])

    def test_malformed_base_sha_fails_closed(self):
        for bad in ('main', self.shas[0][:12], self.shas[0] + '; true', 'HEAD~1'):
            with self.subTest(bad=bad):
                rc, out, _ = self.run_range(base_sha=bad)
                self.assertNotEqual(rc, 0)
                self.assertNotIn('base', out)

    def test_base_outside_history_fails_closed(self):
        rc, out, log = self.run_range(base_sha='0' * 40)
        self.assertNotEqual(rc, 0)
        self.assertNotIn('base', out)

    def test_api_refusal_fails_closed_and_names_the_permission(self):
        rc, out, log = self.run_range(status=403)
        self.assertNotEqual(rc, 0)
        self.assertIn('deployments: read', log)
        self.assertNotIn('base', out)

    def test_bad_environment_name_fails_before_any_request(self):
        for bad in ('', 'web prod', 'web&page=2', '../x'):
            with self.subTest(bad=bad):
                rc, out, _ = self.run_range(environment=bad)
                self.assertNotEqual(rc, 0)
                self.assertEqual(self.api.seen, [])

    def test_older_success_on_a_later_page_is_found(self):
        a, b, c = self.shas
        rows = [(100 + n, b, 'failure') for n in range(30)] + [(7, a, 'success')]
        rc, out, log = self.run_range(deployments={'web-prod': rows})
        self.assertEqual(rc, 0, log)
        self.assertEqual(out.get('base'), a)


if __name__ == '__main__':
    unittest.main()
