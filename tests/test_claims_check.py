"""Run scripts/claims_check.py as a subprocess on diffs made by real git.

python3 -m unittest discover -s tests -v
No mocks: each case builds a temp git repo, commits a base, edits files and
feeds the real `git diff` output to the real script.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / 'scripts' / 'claims_check.py'
GIT_ENV = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull}
IDENT = ['-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
         '-c', 'commit.gpgsign=false']


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], env=GIT_ENV,
                          check=True, capture_output=True, text=True).stdout


def make_diff(files, base='base\n'):
    """files: {path: new content}. The base commit holds `base` at each path."""
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        git(root, 'init', '-q')
        for rel in files:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(base)
        git(root, *IDENT, 'add', '-A')
        git(root, *IDENT, 'commit', '-q', '-m', 'base')
        for rel, content in files.items():
            (root / rel).write_text(content)
        return git(root, 'diff', 'HEAD', '--unified=3')


def run(diff_text, *extra):
    return subprocess.run([sys.executable, str(SCRIPT), *extra], input=diff_text,
                          capture_output=True, text=True)


class ClaimsCheck(unittest.TestCase):
    def test_uncited_guarantee_fails(self):
        proc = run(make_diff({'site/index.md': 'We guarantee 99.99% uptime.\n'}))
        self.assertEqual(proc.returncode, 1, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertGreaterEqual(out['uncited'], 1)
        cats = {h['category'] for h in out['hits']}
        self.assertIn('guarantee', cats)
        self.assertIn('metric', cats)
        hit = out['hits'][0]
        self.assertEqual(hit['file'], 'site/index.md')
        self.assertEqual(hit['line'], 1)
        self.assertFalse(hit['cited'])

    def test_marker_with_evidence_passes(self):
        text = '<!-- claims: proven docs/bench/2026-11.md -->\nWe guarantee 99.99% uptime.\n'
        proc = run(make_diff({'site/index.md': text}))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out['uncited'], 0)
        self.assertTrue(out['hits'] and all(h['cited'] for h in out['hits']))

    def test_marker_same_line_with_url_passes(self):
        text = 'Always on. <!-- claims: proven https://example.invalid/report -->\n'
        proc = run(make_diff({'docs/a.md': text}))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_marker_with_commit_sha_passes(self):
        text = '<!-- claims: proven e04af8f -->\nWe guarantee it.\n'
        proc = run(make_diff({'docs/a.md': text}))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_marker_without_evidence_fails(self):
        text = '<!-- claims: proven -->\nWe guarantee 99.99% uptime.\n'
        proc = run(make_diff({'site/index.md': text}))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertGreaterEqual(json.loads(proc.stdout)['uncited'], 1)

    def test_marker_with_junk_evidence_fails(self):
        text = '<!-- claims: proven trust me -->\nWe guarantee it.\n'
        proc = run(make_diff({'site/index.md': text}))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)

    def test_marker_two_lines_up_does_not_cite(self):
        text = '<!-- claims: proven docs/x.md -->\nHello.\nWe guarantee it.\n'
        proc = run(make_diff({'site/index.md': text}))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)

    def test_code_comment_in_non_public_file_ignored(self):
        text = '// we guarantee this never fails, 100% safe\nfn main() {}\n'
        proc = run(make_diff({'src/lib.rs': text}))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {'hits': [], 'uncited': 0})

    def test_clean_copy_passes(self):
        text = 'Cerebral stores your notes and finds them again.\n'
        proc = run(make_diff({'site/about.md': text}))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['uncited'], 0)

    def test_removed_lines_are_not_checked(self):
        diff = make_diff({'a.md': 'Plain text.\n'}, base='We guarantee it.\n')
        proc = run(diff)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_other_categories_detected(self):
        text = ('We are SOC 2 certified.\nZero downtime.\n'
                'We will refund you and indemnify users.\nPages load 3x faster.\n')
        proc = run(make_diff({'public/page.html': text}))
        self.assertEqual(proc.returncode, 1)
        cats = {h['category'] for h in json.loads(proc.stdout)['hits']}
        self.assertEqual(cats, {'certification', 'absolute', 'liability', 'metric'})

    def test_scope_override(self):
        diff = make_diff({'src/lib.rs': '// we guarantee it\n'})
        proc = run(diff, '--scope', '*.rs')
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)

    def test_garbage_diff_exits_2(self):
        proc = run('this is not a diff\nat all\n')
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, '')
        self.assertIn('bad input', proc.stderr)

    def test_empty_input_exits_2(self):
        proc = run('')
        self.assertEqual(proc.returncode, 2)
        self.assertIn('bad input', proc.stderr)

    def test_missing_file_exits_2(self):
        proc = subprocess.run([sys.executable, str(SCRIPT), '/nonexistent/x.diff'],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn('bad input', proc.stderr)


class ClaimsJobShell(unittest.TestCase):
    """Run the real `claims` job shell against a local fake gateway."""

    @classmethod
    def setUpClass(cls):
        import http.server
        import threading
        import yaml
        wf = yaml.safe_load((SCRIPT.parent.parent / '.github/workflows/agent-jury.yml').read_text())
        cls.script = next(s['run'] for s in wf['jobs']['claims']['steps']
                          if s.get('name', '').startswith('Check claims'))
        cls.state = {'answer': None, 'calls': 0}
        state = cls.state

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                state['calls'] += 1
                if state['answer'] is None:
                    self.send_response(500)
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({'choices': [{'message': {
                    'content': json.dumps(state['answer'])}}]}).encode())

            def log_message(self, *args):
                pass

        cls.server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def run_job(self, text, mode='enforce', answer=None):
        self.state.update(answer=answer, calls=0)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'site').mkdir()
            git(root, 'init', '-q', '-b', 'main')
            (root / 'site/a.md').write_text('x\n')
            git(root, *IDENT, 'add', '-A')
            git(root, *IDENT, 'commit', '-q', '-m', 'base')
            base = git(root, 'rev-parse', 'HEAD').strip()
            (root / 'site/a.md').write_text(text)
            git(root, *IDENT, 'commit', '-qam', 'change')
            os.symlink(SCRIPT.parent.parent, root / '.jury-tools')
            env = {**GIT_ENV, 'LITELLM_URL': 'http://127.0.0.1:%d' % self.server.server_port,
                   'LITELLM_KEY': 'fixture', 'JURY_MODEL': 'm', 'CURL_TIMEOUT_INPUT': '30',
                   'JOB_TIMEOUT_MINUTES': '10', 'DIFF_BUDGET': '60000', 'CLAIMS_MODE': mode,
                   'BASE_REF': 'absent', 'BASE_SHA': base, 'PR_NUMBER': '1',
                   'GITHUB_STEP_SUMMARY': str(root / 'summary.md'), 'RUNNER_TEMP': temp}
            proc = subprocess.run(['bash', '-c', self.script], cwd=root, env=env,
                                  capture_output=True, text=True)
        return proc.returncode, self.state['calls']

    def test_clean_passes_without_model_call(self):
        self.assertEqual(self.run_job('Plain copy.\n'), (0, 0))

    def test_cited_passes_without_model_call(self):
        text = '<!-- claims: proven docs/x.md -->\nWe guarantee it.\n'
        self.assertEqual(self.run_job(text), (0, 0))

    def test_model_guarantee_fails(self):
        ans = {'classifications': [{'index': 0, 'class': 'guarantee'}]}
        self.assertEqual(self.run_job('We guarantee it.\n', answer=ans), (1, 1))

    def test_model_proven_passes(self):
        ans = {'classifications': [{'index': 0, 'class': 'proven'}]}
        self.assertEqual(self.run_job('We guarantee it.\n', answer=ans), (0, 1))

    def test_model_failure_fails_closed(self):
        self.assertEqual(self.run_job('We guarantee it.\n'), (1, 1))

    def test_missing_classification_fails_closed(self):
        self.assertEqual(self.run_job('We guarantee it.\n', answer={'classifications': []}), (1, 1))

    def test_report_mode_never_fails(self):
        self.assertEqual(self.run_job('We guarantee it.\n', mode='report'), (0, 1))


if __name__ == '__main__':
    unittest.main()
