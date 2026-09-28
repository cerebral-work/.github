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

    def test_build_failure_reaches_comment_and_red_delivery_gate(self):
        _, outputs, _, _ = self.run_review(jq_failure=True)
        workflow = yaml.safe_load(WORKFLOW.read_text())
        steps = [s for j in workflow['jobs'].values() for s in j.get('steps', [])]
        comment = next(s['run'] for s in steps if s.get('name') == 'Post review comment + stamp label')
        gate = next(s['run'] for s in steps if s.get('name') == 'Fail when the review was not delivered')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            comment = comment.replace('/tmp/jury-parsed.json', str(root / 'absent-verdict.json'))
            (root / 'curl').write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args[-1].endswith('/comments'):
    Path(os.environ['COMMENT_OUT']).write_text(sys.stdin.read())
if args[-1].endswith('/labels') and '-d' in args:
    Path(os.environ['LABEL_OUT']).write_text(args[args.index('-d') + 1])
""")
            (root / 'curl').chmod(0o755)
            env = dict(os.environ, PATH=temp + os.pathsep + os.environ['PATH'],
                       GH_TOKEN='fixture', PR_NUMBER='1', REPO='fixture/repo', MODEL='full',
                       VERDICT='', CONFIDENCE='', FINDINGS_COUNT='',
                       REVIEW_FAILED=outputs.get('review_failed', ''),
                       FAILURE_REASON=outputs.get('failure_reason', ''),
                       COMMENT_OUT=str(root / 'comment'), LABEL_OUT=str(root / 'label'))
            proc = subprocess.run(['bash', '-e', '-c', comment], env=env, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            body = json.loads((root / 'comment').read_text())['body']
            self.assertIn('request build failed (exit 5)', body)
            self.assertEqual(json.loads((root / 'label').read_text()), {'labels': ['agent-jury-error']})
            proc = subprocess.run(['bash', '-e', '-c', gate], env=env, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn('request build failed (exit 5)', proc.stdout)

    def test_invalid_completion_reports_failure(self):
        proc, outputs, _, _ = self.run_review(response={'choices': [{'message': {'content': 'not JSON'}}]})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(outputs.get('review_failed'), 'true')
        self.assertIn('completion JSON parse failed', outputs['failure_reason'])
        self.assertIn('parse error', proc.stderr)
        self.assertNotIn('verdict', outputs)
