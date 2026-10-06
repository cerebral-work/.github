"""Run the real workflow shell offline, with real jq and a fake HTTP transport.

python3 -m unittest discover -s tests -v
JURY_WORKFLOW=/path/to/old.yml exercises the same assertions as a break-check.
No credentials, GitHub calls, gateway calls, or private source fixtures.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

WORKFLOW = Path(os.environ.get('JURY_WORKFLOW', '.github/workflows/agent-jury.yml'))


class Review(unittest.TestCase):
    def run_review(self, response=None, *, jq_failure=False, large=False, full=True, http_code="200"):
        workflow = yaml.safe_load(WORKFLOW.read_text())
        script = next(step['run'] for job in workflow['jobs'].values()
                      for step in job.get('steps', []) if step.get('id') == 'review')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('pr-diff.txt', 'glm-response.json', 'jury-parsed.json'):
                script = script.replace('/tmp/' + name, str(root / name))
            # Full-model control (6 Rust files, 199 lines); shell metacharacters
            # stay data. Large mode also exceeds Linux's per-argument limit.
            diff = ('+literal `code` $VAR "quoted" \\ unicode é\n' * 199)
            if large:
                diff *= 30
            (root / 'pr-diff.txt').write_text(diff)
            (root / 'outputs').touch()
            if response is None:
                response = {'choices': [{'message': {'content': json.dumps({
                    'verdict': 'approved', 'confidence': 'high',
                    'findings': [], 'summary': 'fixture'})}}]}
            (root / 'response').write_text(json.dumps(response))
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            # Capture the exact serialized request before its temp dir is removed.
            (bin_dir / 'curl').write_text('''#!/usr/bin/env python3
import os, sys
from pathlib import Path
args = sys.argv[1:]
root = Path(os.environ['FIXTURE_ROOT'])
key = '--data-binary' if '--data-binary' in args else '-d'
body = args[args.index(key) + 1]
(root / 'request').write_text(Path(body[1:]).read_text() if body.startswith('@') else body)
Path(args[args.index('-o') + 1]).write_bytes((root / 'response').read_bytes())
print(os.environ['FIXTURE_HTTP_CODE'], end='')
''')
            (bin_dir / 'curl').chmod(0o755)
            if jq_failure:
                (bin_dir / 'jq').write_text('''#!/usr/bin/env bash
if [[ "$1" == "-nc" ]]; then
  echo 'jq: error (at <unknown>): injected request-build failure' >&2
  exit 5
fi
exec "''' + shutil.which('jq') + '''" "$@"
''')
                (bin_dir / 'jq').chmod(0o755)
            env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'],
                       FIXTURE_ROOT=temp, FIXTURE_HTTP_CODE=http_code, RUNNER_TEMP=temp, GITHUB_OUTPUT=str(root / 'outputs'),
                       PR_NUMBER='1', PR_TITLE='Synthetic review',
                       FILES_CHANGED='\n'.join(f'file{i}.rs' for i in range(6)) if full else 'README.md',
                       DIFF_NOTE='complete', JURY_MODEL_FULL='full', JURY_MODEL_FAST='fast',
                       CURL_TIMEOUT_INPUT='0', JOB_TIMEOUT_MINUTES='10',
                       LITELLM_URL='http://fixture.invalid', LITELLM_KEY='', AGENT_JURY_GATEWAY_KEY='')
            proc = subprocess.run(['bash', '-e', '-c', script], env=env,
                                  capture_output=True, text=True)
            outputs = dict(line.split('=', 1) for line in (root / 'outputs').read_text().splitlines())
            request = json.loads((root / 'request').read_text()) if (root / 'request').exists() else None
            self.assertEqual(list(root.glob('agent-jury-request.*')), [], 'request temp files leaked')
            return proc, outputs, request, diff

    def test_full_and_fast_success(self):
        for full in (True, False):
            with self.subTest(full=full):
                proc, outputs, request, diff = self.run_review(full=full)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(outputs['verdict'], 'approved')
                self.assertNotIn('review_failed', outputs)
                self.assertEqual(request['model'], 'full' if full else 'fast')
                self.assertIn(diff.rstrip('\n'), request['messages'][0]['content'])
                self.assertEqual(request['max_tokens'], 64000)

    def test_large_prompt_is_not_an_argument(self):
        proc, outputs, request, diff = self.run_review(large=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(outputs.get('verdict'), 'approved', proc.stderr)
        self.assertIn(diff.rstrip('\n'), request['messages'][0]['content'])

    def test_request_build_exit_five_reports_failure(self):
        proc, outputs, request, _ = self.run_review(jq_failure=True)
        self.assertEqual(outputs.get('review_failed'), 'true', (proc.returncode, outputs, proc.stderr))
        self.assertEqual(outputs['failure_reason'], 'request build failed (exit 5)')
        self.assertEqual(proc.returncode, 0)
        self.assertIn('jq: error', proc.stderr)
        self.assertNotIn('verdict', outputs)
        self.assertIsNone(request, 'failed builds must not reach curl')

    def test_response_decode_exit_five_reports_failure(self):
        proc, outputs, _, _ = self.run_review(response='wrong-shaped response')
        self.assertEqual(outputs.get('review_failed'), 'true', (proc.returncode, outputs, proc.stderr))
        self.assertEqual(outputs['failure_reason'], 'response decode failed (exit 5)')
        self.assertIn('Cannot index string with string "choices"', proc.stderr)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn('verdict', outputs)

    def test_transport_error_still_uses_existing_tier(self):
        proc, outputs, _, _ = self.run_review(http_code='503')
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(outputs.get('review_failed'), 'true')
        self.assertEqual(outputs['failure_reason'], 'HTTP 503')
        self.assertNotIn('verdict', outputs)

    def run_comment_step(self, outputs, *, label_code='200', labels_after=None, verdict='',
                         parsed=None):
        """Run the comment+label step against a fake GitHub API.

        The fake records each write, answers `-w '%{http_code}'` like real curl,
        and serves a label list for the read-back GET.
        """
        workflow = yaml.safe_load(WORKFLOW.read_text())
        steps = [s for j in workflow['jobs'].values() for s in j.get('steps', [])]
        comment = next(s['run'] for s in steps if s.get('name') == 'Post review comment + stamp label')
        temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp)
        root = Path(temp)
        verdict_file = root / 'verdict.json'
        if parsed is not None:
            verdict_file.write_text(json.dumps(parsed))
        comment = comment.replace('/tmp/jury-parsed.json', str(verdict_file))
        (root / 'curl').write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
url = args[-1]
method = args[args.index('-X') + 1] if '-X' in args else 'GET'
root = Path(os.environ['FIXTURE_ROOT'])
with open(root / 'calls', 'a') as f:
    f.write(method + ' ' + url + '\\n')
out = Path(args[args.index('-o') + 1]) if '-o' in args else None
body, code = '{}', '200'
if method == 'POST' and url.endswith('/comments'):
    src = args[args.index('--data-binary') + 1]
    (root / 'comment').write_text(Path(src[1:]).read_text())
    code = '201'
elif method == 'POST' and url.endswith('/labels'):
    (root / 'label').write_text(args[args.index('-d') + 1])
    code = os.environ['FIXTURE_LABEL_CODE']
    if code != '200':
        body = '{"message":"Label does not exist"}'
elif method == 'DELETE':
    code = '404'
elif method == 'GET' and '/labels' in url:
    body = os.environ['FIXTURE_LABELS_AFTER']
if out is not None:
    out.write_text(body)
else:
    sys.stdout.write(body)
if '-w' in args:
    sys.stdout.write(code)
""")
        (root / 'curl').chmod(0o755)
        if labels_after is None:
            labels_after = []
        env = dict(os.environ, PATH=temp + os.pathsep + os.environ['PATH'],
                   FIXTURE_ROOT=temp, RUNNER_TEMP=temp,
                   FIXTURE_LABEL_CODE=label_code,
                   FIXTURE_LABELS_AFTER=json.dumps([{'name': n} for n in labels_after]),
                   GH_TOKEN='fixture', PR_NUMBER='1', REPO='fixture/repo', MODEL='full',
                   VERDICT=verdict, CONFIDENCE='high' if verdict else '',
                   FINDINGS_COUNT='0' if verdict else '',
                   REVIEW_FAILED=outputs.get('review_failed', ''),
                   FAILURE_REASON=outputs.get('failure_reason', ''))
        proc = subprocess.run(['bash', '-e', '-c', comment], env=env, capture_output=True, text=True)
        return proc, root

    def test_build_failure_reaches_comment_and_red_delivery_gate(self):
        _, outputs, _, _ = self.run_review(jq_failure=True)
        proc, root = self.run_comment_step(outputs, labels_after=['agent-jury-error'])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads((root / 'comment').read_text())['body']
        self.assertIn('request build failed (exit 5)', body)
        self.assertEqual(json.loads((root / 'label').read_text()), {'labels': ['agent-jury-error']})
        self.assertNotIn('::warning::', proc.stdout)
        workflow = yaml.safe_load(WORKFLOW.read_text())
        steps = [s for j in workflow['jobs'].values() for s in j.get('steps', [])]
        gate = next(s['run'] for s in steps if s.get('name') == 'Fail when the review was not delivered')
        env = dict(os.environ, FAILURE_REASON=outputs.get('failure_reason', ''))
        proc = subprocess.run(['bash', '-e', '-c', gate], env=env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn('request build failed (exit 5)', proc.stdout)

    def test_missing_label_warns_instead_of_vanishing(self):
        # CER-2088: the repo lacks the label, so GitHub answers 422 and the PR
        # ends up unlabelled. That must show in the run log, not disappear.
        proc, root = self.run_comment_step(
            {}, label_code='422', labels_after=[], verdict='changes-requested',
            parsed={'verdict': 'changes-requested', 'findings': [], 'summary': 's'})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('::warning::agent jury: stamp label agent-jury-changes-requested failed (HTTP 422)',
                      proc.stdout)
        self.assertIn('Label does not exist', proc.stdout)
        self.assertIn("expected exactly label agent-jury-changes-requested on the PR, found 'none'",
                      proc.stdout)
        self.assertIn('gh label create agent-jury-changes-requested', proc.stdout)

    def test_stale_second_label_is_reported(self):
        # Removal failed silently before; a PR carrying two verdict labels must warn.
        proc, _ = self.run_comment_step(
            {}, labels_after=['agent-jury-approved', 'agent-jury-needs-changes'],
            verdict='approved', parsed={'verdict': 'approved', 'findings': [], 'summary': 's'})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("found 'agent-jury-approved,agent-jury-needs-changes'", proc.stdout)

    def test_clean_stamp_is_quiet_and_404_removals_are_normal(self):
        proc, root = self.run_comment_step(
            {}, labels_after=['agent-jury-approved'], verdict='approved',
            parsed={'verdict': 'approved', 'findings': [], 'summary': 's'})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn('::warning::', proc.stdout)
        self.assertIn('on PR: agent-jury-approved', proc.stdout)
        calls = (root / 'calls').read_text().splitlines()
        self.assertEqual(sum(c.startswith('DELETE ') for c in calls), 4)

    def test_invalid_completion_reports_failure(self):
        proc, outputs, _, _ = self.run_review(response={'choices': [{'message': {'content': 'not JSON'}}]})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(outputs.get('review_failed'), 'true')
        self.assertIn('completion JSON parse failed', outputs['failure_reason'])
        self.assertIn('parse error', proc.stderr)
        self.assertNotIn('verdict', outputs)
