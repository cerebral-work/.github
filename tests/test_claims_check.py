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
                          check=True, capture_output=True, text=True,
                          errors='surrogateescape').stdout


_KEPT = []


def tearDownModule():
    for tmp in _KEPT:
        tmp.cleanup()


def make_diff(files, base='base\n', extra=None, keep=False):
    """files: {path: new content}. The base commit holds `base` at each path
    and `extra` ({path: content}) unchanged. The edit is committed after the
    diff is taken, so HEAD is the diffed head. keep=True returns
    (diff, repo_root) and leaves the repo until the module ends."""
    tmp = tempfile.TemporaryDirectory()
    if keep:
        _KEPT.append(tmp)
    root = Path(tmp.name)
    try:
        git(root, 'init', '-q')
        for rel in files:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(base)
        for rel, content in (extra or {}).items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        git(root, *IDENT, 'add', '-A')
        git(root, *IDENT, 'commit', '-q', '-m', 'base')
        for rel, content in files.items():
            if isinstance(content, bytes):
                (root / rel).write_bytes(content)
            else:
                (root / rel).write_text(content)
        diff = git(root, 'diff', 'HEAD', '--unified=3')
        git(root, *IDENT, 'commit', '-q', '-am', 'head')
        return (diff, root) if keep else diff
    finally:
        if not keep:
            tmp.cleanup()


EVIDENCE = {'docs/x.md': 'evidence\n'}


def run_repo(files, *extra, base='base\n', evidence=None):
    """Run the script with --repo on a repo that holds the evidence files."""
    diff, root = make_diff(files, base=base, extra=evidence or EVIDENCE, keep=True)
    proc = run(diff, '--repo', str(root), '--base', 'HEAD~1', *extra)
    proc.root = root
    return proc


def build(base_files, head_files, base_links=None, side=None, head_links=None):
    """Flexible repo builder. Returns (diff, root, info). The diff is
    `git diff --cached HEAD` of the head edit (new files included). info has
    base, head and side commit ids. The repo lives until the module ends."""
    tmp = tempfile.TemporaryDirectory()
    _KEPT.append(tmp)
    root = Path(tmp.name)
    git(root, 'init', '-q', '-b', 'main')

    def write(files, links):
        for rel, content in (files or {}).items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.is_symlink() or p.exists():
                p.unlink()
            if isinstance(content, bytes):
                p.write_bytes(content)
            else:
                p.write_text(content)
        for rel, target in (links or {}).items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(target, p)
    write(base_files, base_links)
    git(root, *IDENT, 'add', '-A')
    git(root, *IDENT, 'commit', '-q', '-m', 'base')
    info = {'base': git(root, 'rev-parse', 'HEAD').strip()}
    if side:
        git(root, 'switch', '-q', '-c', 'side')
        write(side, None)
        git(root, *IDENT, 'add', '-A')
        git(root, *IDENT, 'commit', '-q', '-m', 'side')
        info['side'] = git(root, 'rev-parse', 'HEAD').strip()
        git(root, 'switch', '-q', 'main')
    write(head_files, head_links)
    git(root, *IDENT, 'add', '-A')
    diff = git(root, 'diff', '--cached', 'HEAD', '--unified=3')
    git(root, *IDENT, 'commit', '-q', '-m', 'head')
    info['head'] = git(root, 'rev-parse', 'HEAD').strip()
    return diff, root, info


def run(diff_text, *extra, cwd=None, env=None):
    proc = subprocess.run([sys.executable, str(SCRIPT), *extra],
                          input=diff_text.encode('utf-8', 'surrogateescape'),
                          capture_output=True, cwd=cwd, env=env)
    proc.stdout = proc.stdout.decode('utf-8', 'replace')
    proc.stderr = proc.stderr.decode('utf-8', 'replace')
    return proc


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
        text = '<!-- claims: proven docs/x.md -->\nWe guarantee it.\n'
        proc = run_repo({'site/index.md': text})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out['uncited'], 0)
        self.assertTrue(out['hits'] and all(h['cited'] for h in out['hits']))

    def test_marker_same_line_with_url_passes(self):
        text = 'Always on. <!-- claims: proven https://github.com/o/r/pull/1 -->\n'
        proc = run_repo({'docs/a.md': text}, '--url-hosts', 'github.com')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_marker_with_commit_sha_passes(self):
        diff, root = make_diff({'docs/a.md': '<!-- claims: proven %s -->\nWe guarantee it.\n'}, keep=True)
        sha = git(root, 'rev-parse', '--short=7', 'HEAD~1').strip()
        proc = run(diff.replace('%s', sha), '--repo', str(root), '--base', 'HEAD~1')
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
        proc = run_repo({'site/index.md': text})
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


class Unreadable(unittest.TestCase):
    def test_utf16_in_public_path_is_a_hit(self):
        text = 'We guarantee it.\n'.encode('utf-16')
        diff = make_diff({'site/a.md': text, 'docs/b.md': 'fine\n'})
        proc = run(diff)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn('unreadable', {h['category'] for h in out['hits']})
        self.assertEqual({h['file'] for h in out['hits']}, {'site/a.md'})

    def test_nul_byte_in_public_path_is_a_hit(self):
        diff = make_diff({'site/a.md': b'We guarantee it.\x00\n'})
        proc = run(diff)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn('unreadable', {h['category'] for h in json.loads(proc.stdout)['hits']})

    def test_undecodable_text_in_public_path_is_a_hit(self):
        diff = make_diff({'site/a.md': b'caf\xe9 plain latin-1 text\n'})
        proc = run(diff)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn('unreadable', {h['category'] for h in json.loads(proc.stdout)['hits']})

    def test_binary_outside_scope_exits_0(self):
        diff = make_diff({'bin/x.dat': b'\x00\x01\x02\n'})
        proc = run(diff)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {'hits': [], 'uncited': 0})

    def test_pure_rename_and_mode_change_exit_0(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git(root, 'init', '-q')
            (root / 'a.txt').write_text('line one\nline two\nline three\n')
            (root / 'run.sh').write_text('x\n')
            git(root, *IDENT, 'add', '-A')
            git(root, *IDENT, 'commit', '-q', '-m', 'base')
            git(root, 'mv', 'a.txt', 'b.txt')
            os.chmod(root / 'run.sh', 0o755)
            git(root, 'add', '-A')
            diff = git(root, 'diff', '--cached', '-M')
        self.assertNotIn('@@', diff)
        proc = run(diff)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {'hits': [], 'uncited': 0})


class Scope(unittest.TestCase):
    def assert_in_scope(self, path):
        proc = run(make_diff({path: 'We guarantee it.\n'}))
        self.assertEqual(proc.returncode, 1, path + ' ' + proc.stdout + proc.stderr)

    def assert_out_of_scope(self, path):
        proc = run(make_diff({path: 'We guarantee it.\n'}))
        self.assertEqual(proc.returncode, 0, path + ' ' + proc.stdout + proc.stderr)

    def test_added_extensions_and_dirs(self):
        for path in ('a.htm', 'a.markdown', 'a.rst', 'x/y/README', 'README.txt',
                     'pkg/Readme.md', 'pricing/plans.txt', 'landing/a.txt'):
            with self.subTest(path=path):
                self.assert_in_scope(path)

    def test_case_insensitive(self):
        for path in ('A.MD', 'a/B.Html', 'Site/a.txt', 'DOCS/a.txt', 'Pricing/x.json'):
            with self.subTest(path=path):
                self.assert_in_scope(path)

    def test_round3_default_scope(self):
        for path in ('locales/en.json', 'src/i18n/en.json', 'page.njk', 'a/b.liquid',
                     'docs/a.txt', 'public/a.json', 'site/data/copy.json'):
            with self.subTest(path=path):
                self.assert_in_scope(path)
        for path in ('x.yaml', 'x.ts', 'LICENSE', 'LICENSE.md', 'legal/LICENSE.txt',
                     'page.txt', 'page.json', 'assets/copy.txt', 'COPYING'):
            with self.subTest(path=path):
                self.assert_out_of_scope(path)

    def test_scope_list_input_replaces_the_default(self):
        text = 'We guarantee it.\n'
        proc = run(make_diff({'src/a/b.ts': text}), '--scope-list', 'src/**/*.ts, copy/*.yaml')
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        proc = run(make_diff({'README.md': text}), '--scope-list', 'src/**/*.ts')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_yaml_and_ts_outside_listed_dirs_stay_out(self):
        for path in ('conf/a.yaml', 'src/a.ts', 'src/a.txt'):
            with self.subTest(path=path):
                self.assert_out_of_scope(path)


class Lexicon(unittest.TestCase):
    CASES = [
        ('This is zero risk.', 'absolute'),
        ('It is risk-free.', 'absolute'),
        ('The service will not fail.', 'absolute'),
        ("The service won't fail.", 'absolute'),
        ('We reach five nines.', 'metric'),
        ('We reach 5 nines.', 'metric'),
        ('Served 99.99% of requests.', 'metric'),
        ('Up 99.9% of the time.', 'metric'),
        ('A money-back offer.', 'liability'),
        ('We hold harmless our users.', 'liability'),
        ('We hold you harmless.', 'liability'),
        ('We are HITRUST ready.', 'certification'),
        ('Uses military-grade encryption.', 'absolute'),
        ('Uses bank-grade encryption.', 'absolute'),
    ]

    def test_each_term_hits_in_its_category(self):
        for text, cat in self.CASES:
            with self.subTest(text=text):
                proc = run(make_diff({'site/a.md': text + '\n'}))
                self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                self.assertIn(cat, {h['category'] for h in json.loads(proc.stdout)['hits']})

    def test_plain_decimal_text_without_percent_is_clean(self):
        proc = run(make_diff({'site/a.md': 'Version 1.5 ships on the 3rd.\n'}))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


ZW = chr(0x200b)
SHY = chr(0x00ad)
CYR_A = chr(0x0430)
GREEK_O = chr(0x03bf)


class LexiconRound3(unittest.TestCase):
    def test_new_terms_hit(self):
        for text in ('We will pay damages.', 'You will be reimbursed.', 'Audited by a firm.',
                     'It cannot be breached.', "It can't be breached.", 'It is secure by design.',
                     'A tamper-proof log.', 'A tamperproof log.', 'Formally verified code.',
                     'There is no risk.', 'This is patented.'):
            with self.subTest(text=text):
                proc = run(make_diff({'site/a.md': text + '\n'}))
                self.assertEqual(proc.returncode, 1, text + proc.stdout + proc.stderr)


class Round3Evasions(unittest.TestCase):
    def rc(self, text, **kw):
        proc = run(make_diff({'site/a.md': text + '\n'}, **kw))
        return proc.returncode, proc.stdout + proc.stderr

    def test_markup_inside_a_word_does_not_hide_it(self):
        for text in ('We guar<!-- -->antee it.', 'We guar<b>an</b>tee it.',
                     'We guar**an**tee it.', 'We guar__an__tee it.', 'We guar\\antee it.',
                     'We [guar](x)[antee](y) it.', 'We _guarantee_ it.',
                     'We [guarantee](https://x.example/p) it.'):
            with self.subTest(text=text):
                rc, out = self.rc(text)
                self.assertEqual(rc, 1, out)

    def test_markup_control_clean_copy_passes(self):
        rc, out = self.rc('<b>Plain</b> [docs](https://x.example/a) with *emphasis* and snake_case.')
        self.assertEqual(rc, 0, out)

    def test_invisible_non_cf_characters_do_not_hide_a_word(self):
        for cp in (0x0301, 0xFE0F, 0x180B, 0x3164, 0x2800, 0x115F, 0x1160, 0xFFA0):
            for text in ('We guar%santee it.', 'We guarantee%s it.'):
                with self.subTest(cp=hex(cp), text=text):
                    rc, out = self.rc(text % chr(cp))
                    self.assertEqual(rc, 1, out)

    def test_accented_copy_is_clean(self):
        rc, out = self.rc('Le r\u00e9sum\u00e9 de la soci\u00e9t\u00e9 est pr\u00eat.')
        self.assertEqual(rc, 0, out)

    def test_whole_word_lookalikes_fold_to_latin(self):
        for text in ('We are \u041d\u0406\u0420\u0410\u0410 ready.',
                     'We are \u0405\u041e\u0421 2 ready.', 'We are \u0420\u0421\u0406 ready.'):
            with self.subTest(text=text):
                rc, out = self.rc(text)
                self.assertEqual(rc, 1, out)
                self.assertIn('certification', out)

    def test_plain_russian_copy_is_clean(self):
        rc, out = self.rc('\u041f\u0440\u0438\u0432\u0435\u0442, \u043c\u0438\u0440. \u042d\u0442\u043e \u0442\u0435\u043a\u0441\u0442.')
        self.assertEqual(rc, 0, out)


class SplitLines(unittest.TestCase):
    def test_claim_split_over_two_added_lines_is_found_on_the_first(self):
        for a, b in (('This service is fully', 'secure for all users.'), ('We guar-', 'antee it.')):
            with self.subTest(a=a):
                proc = run(make_diff({'site/a.md': a + '\n' + b + '\n'}))
                self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                hits = json.loads(proc.stdout)['hits']
                self.assertEqual([h['line'] for h in hits], [1], hits)

    def test_a_hit_on_one_line_is_not_reported_twice(self):
        proc = run(make_diff({'site/a.md': 'We guarantee it.\nplain text.\n'}))
        self.assertEqual(len(json.loads(proc.stdout)['hits']), 1, proc.stdout)

    def test_lines_with_context_between_do_not_join(self):
        proc = run(make_diff({'site/a.md': 'fully\nmiddle\nsecure\n'}, base='a\nmiddle\nc\n'))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_marker_cites_a_split_claim_on_the_next_line_pair(self):
        text = '<!-- claims: proven docs/x.md -->\nfully\nsecure\n'
        proc = run_repo({'site/a.md': text})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class Obfuscation(unittest.TestCase):
    def hits(self, text, code=1):
        proc = run(make_diff({'site/a.md': text + '\n'}))
        self.assertEqual(proc.returncode, code, repr(text) + proc.stdout + proc.stderr)
        return json.loads(proc.stdout)['hits']

    def test_lexicon_matches_after_normalisation(self):
        cases = {
            'gua' + ZW + 'rantee': 'guarantee',
            'guar' + SHY + 'antee': 'guarantee',
            'gu' + CYR_A + 'rantee': 'guarantee',
            '&#103;uarantee': 'guarantee',
            chr(0xff11) + chr(0xff10) + chr(0xff10) + chr(0xff05) + ' uptime': 'metric',
            '100%secure': 'absolute',
            'fully  secure': 'absolute',
            'fully-secure': 'absolute',
            'fully\tsecure': 'absolute',
            'zero-risk': 'absolute',
        }
        for text, cat in cases.items():
            with self.subTest(text=text):
                self.assertIn(cat, {h['category'] for h in self.hits(text)})

    def test_original_text_is_reported(self):
        hits = self.hits('We say fully-secure here.')
        self.assertEqual(hits[0]['text'], 'We say fully-secure here.')

    def test_zero_width_or_soft_hyphen_line_is_an_obfuscation_hit(self):
        for text in ('Nice ' + ZW + 'word.', 'Nice wo' + SHY + 'rd.', chr(0xfeff) + 'Plain.'):
            with self.subTest(text=text):
                self.assertIn('obfuscation', {h['category'] for h in self.hits(text)})

    def test_mixed_script_word_is_an_obfuscation_hit(self):
        for text in ('The w' + CYR_A + 'rd is odd.', 'The w' + GREEK_O + 'rd is odd.'):
            with self.subTest(text=text):
                self.assertIn('obfuscation', {h['category'] for h in self.hits(text)})

    def test_plain_non_latin_text_is_clean(self):
        self.hits(chr(0x043f) + chr(0x0440) + chr(0x0438) + chr(0x0432) + chr(0x0435) + chr(0x0442), code=0)
        self.hits('Caf' + chr(0xe9) + ' menu.', code=0)

    def test_hyphenated_ordinary_words_are_clean(self):
        self.hits('A well-known, easy-to-read page.', code=0)


class Markers(unittest.TestCase):
    def fails(self, text, *extra, evidence=None):
        proc = run_repo({'site/a.md': text + '\n'}, *extra, evidence=evidence)
        self.assertEqual(proc.returncode, 1, repr(text) + proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def passes(self, text, *extra, evidence=None):
        proc = run_repo({'site/a.md': text + '\n'}, *extra, evidence=evidence)
        self.assertEqual(proc.returncode, 0, repr(text) + proc.stdout + proc.stderr)

    def test_unresolvable_evidence_does_not_cite(self):
        for ev in ('x.y', 'no/such/file.md', 'http://x', '0000000', 'docs/', '../docs/x.md',
                   'https://example.invalid/r'):
            with self.subTest(evidence=ev):
                self.fails('We guarantee it. <!-- claims: proven %s -->' % ev)

    def test_real_path_and_real_sha_cite(self):
        self.passes('We guarantee it. <!-- claims: proven docs/x.md -->')
        self.passes('We guarantee it. <!-- claims: proven ./docs/x.md -->')
        diff, root = make_diff({'site/a.md': 'We guarantee it. claims: proven SHA\n'},
                               extra=EVIDENCE, keep=True)
        for sha in (git(root, 'rev-parse', 'HEAD~1').strip(),
                    git(root, 'rev-parse', '--short=7', 'HEAD~1').strip()):
            with self.subTest(sha=sha):
                proc = run(diff.replace('SHA', sha), '--repo', str(root), '--base', 'HEAD~1')
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_without_repo_nothing_resolves(self):
        diff = make_diff({'site/a.md': 'We guarantee it. <!-- claims: proven docs/x.md -->\n'},
                         extra=EVIDENCE)
        self.assertEqual(run(diff).returncode, 1)

    def test_marker_cites_exactly_one_hit(self):
        out = self.fails('claims: proven docs/x.md We guarantee A, 100% uptime, SOC 2, indemnify you.')
        self.assertTrue(all(not h['cited'] for h in out['hits']))
        self.fails('claims: proven x.y We guarantee A, 100% uptime')
        out = self.fails('We guarantee A, 100% uptime <!-- claims: proven docs/x.md -->')
        # One claim: the overlapping hits `100%` and `100% uptime`. The
        # guarantee on the same line stays uncited.
        cited = {h['term'].lower() for h in out['hits'] if h['cited']}
        self.assertEqual(cited, {'100%', '100% uptime'}, out)
        out = self.fails('<!-- claims: proven docs/x.md -->\nWe guarantee 100% uptime, SOC 2.')
        self.assertEqual(sum(1 for h in out['hits'] if h['cited']), 1, out)

    def test_marker_with_no_hit_before_it_cites_nothing_on_its_own_line(self):
        self.fails('<!-- claims: proven docs/x.md --> We guarantee it.')

    def test_next_line_single_claim_passes(self):
        self.passes('<!-- claims: proven docs/x.md -->\nWe guarantee it.')

    def test_self_cite_does_not_count(self):
        self.fails('We guarantee claims: proven a/b')
        self.fails('noclaims: proven docs/x.md\nWe guarantee it.')
        self.fails('We guarantee it noclaims: proven docs/x.md')
        self.fails('We guarantee it x-claims: proven docs/x.md')

    def test_marker_on_a_non_claim_line_does_not_cite_the_next_line(self):
        self.fails('plain text claims: proven docs/x.md\nWe guarantee it.')
        self.fails('<!-- note --> claims: proven docs/x.md\nWe guarantee it.')
        self.fails('see claims: proven docs/x.md -->\nWe guarantee it.')

    def test_standalone_marker_forms_cite_the_next_line(self):
        for text in ('claims: proven docs/x.md\nWe guarantee it.',
                     '<!-- claims: proven docs/x.md -->\nWe guarantee it.',
                     '{/* claims: proven docs/x.md */}\nWe guarantee it.',
                     '// claims: proven docs/x.md\nWe guarantee it.',
                     '# claims: proven docs/x.md\nWe guarantee it.'):
            with self.subTest(text=text):
                self.passes(text)

    def test_marker_token_after_comment_openers(self):
        for text in ('// claims: proven docs/x.md\nWe guarantee it.',
                     '# claims: proven docs/x.md\nWe guarantee it.',
                     '{/* claims: proven docs/x.md */}\nWe guarantee it.',
                     '<!--claims: proven docs/x.md-->\nWe guarantee it.',
                     '  claims: proven docs/x.md\nWe guarantee it.'):
            with self.subTest(text=text):
                self.passes(text)


class UrlEvidence(unittest.TestCase):
    def cite(self, url, hosts='github.com'):
        args = ['--url-hosts', hosts] if hosts is not None else []
        proc = run_repo({'site/a.md': 'We guarantee it. <!-- claims: proven %s -->\n' % url}, *args)
        return proc.returncode == 0

    def test_allowed_https_host_cites(self):
        self.assertTrue(self.cite('https://github.com/o/r/pull/1'))
        self.assertTrue(self.cite('https://GitHub.com/x'))

    def test_everything_else_does_not_cite(self):
        for url in ('http://github.com/x', 'https://evil.example/x',
                    'https://github.com.evil.example/x', 'https://user@github.com/x',
                    'https://user:pw@github.com/x', 'https://127.0.0.1/x',
                    'https://github.com:443/x', 'https://github.com:8443/x',
                    'https://[::1]/x', 'https://xn--gthub-gua.com/x',
                    'https://github.com./x', 'ftp://github.com/x'):
            with self.subTest(url=url):
                self.assertFalse(self.cite(url), url)

    def test_no_allowlist_means_no_url_cites(self):
        self.assertFalse(self.cite('https://github.com/x', hosts=None))
        self.assertFalse(self.cite('https://github.com/x', hosts=''))

    def test_ip_and_idna_host_cite_only_when_listed_exactly(self):
        self.assertTrue(self.cite('https://127.0.0.1/x', hosts='127.0.0.1'))
        self.assertTrue(self.cite('https://xn--gthub-gua.com/x', hosts='xn--gthub-gua.com'))
        self.assertFalse(self.cite('https://127.0.0.2/x', hosts='127.0.0.1'))

    def test_list_of_hosts(self):
        self.assertTrue(self.cite('https://example.org/x', hosts='github.com, example.org'))

    def test_url_is_never_fetched(self):
        # A closed local port: a fetch would be refused, not accepted. The host
        # is listed, so the URL cites without any request.
        self.assertTrue(self.cite('https://127.0.0.1/never', hosts='127.0.0.1'))


class EvidenceRules(unittest.TestCase):
    CLAIM = 'We guarantee it. claims: proven %s\n'

    def rc(self, evidence, base_files=None, head_files=None, extra=(), **kw):
        base_files = {'docs/x.md': 'evidence\n', 'site/a.md': 'x\n', **(base_files or {})}
        head_files = {'site/a.md': self.CLAIM % evidence, **(head_files or {})}
        diff, root, info = build(base_files, head_files, **kw)
        proc = run(diff, '--repo', str(root), '--base', info['base'], *extra)
        return proc.returncode

    def test_control_regular_base_file_cites(self):
        self.assertEqual(self.rc('docs/x.md'), 0)

    def test_claims_own_file_does_not_cite(self):
        self.assertEqual(self.rc('site/a.md'), 1)

    def test_file_the_pr_changes_does_not_cite(self):
        self.assertEqual(self.rc('docs/x.md', head_files={'docs/x.md': 'changed\n'}), 1)

    def test_file_the_pr_adds_does_not_cite(self):
        self.assertEqual(self.rc('docs/new.md', head_files={'docs/new.md': 'new evidence\n'}), 1)

    def test_directory_empty_file_and_symlink_do_not_cite(self):
        base = {'docs/sub/f.md': 'e\n', 'docs/empty.md': ''}
        self.assertEqual(self.rc('docs/sub', base_files=base), 1)
        self.assertEqual(self.rc('docs/empty.md', base_files=base), 1)
        self.assertEqual(self.rc('docs/link.md', base_links={'docs/link.md': 'x.md'}), 1)

    def test_sha_must_be_reachable_from_base(self):
        diff, root, info = build({'docs/x.md': 'e\n', 'site/a.md': 'x\n'},
                                 {'site/a.md': 'We guarantee it. claims: proven HEAD1\n'},
                                 side={'other.txt': 'side\n'})
        for name, ok in (('base', True), ('side', False), ('head', False)):
            with self.subTest(sha=name):
                proc = run(diff.replace('HEAD1', info[name]), '--repo', str(root),
                           '--base', info['base'])
                self.assertEqual(proc.returncode, 0 if ok else 1, proc.stdout + proc.stderr)

    def test_without_base_nothing_resolves(self):
        diff, root, info = build({'docs/x.md': 'e\n', 'site/a.md': 'x\n'},
                                 {'site/a.md': self.CLAIM % 'docs/x.md'})
        self.assertEqual(run(diff, '--repo', str(root)).returncode, 1)

    def test_evidence_paths_globs(self):
        base = {'docs/bench/a.md': 'b\n'}
        args = ('--evidence-paths', 'docs/bench/*.md, legal/*.pdf')
        self.assertEqual(self.rc('docs/x.md', base_files=base, extra=args), 1)
        self.assertEqual(self.rc('docs/bench/a.md', base_files=base, extra=args), 0)
        self.assertEqual(self.rc('docs/x.md', base_files=base), 0)


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

    def run_job(self, text, mode='enforce', answer=None, flags='-e', base_files=None,
                mv=None, extra_env=None):
        self.state.update(answer=answer, calls=0)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'site').mkdir()
            (root / 'docs').mkdir()
            (root / 'docs/x.md').write_text('evidence\n')
            git(root, 'init', '-q', '-b', 'main')
            (root / 'site/a.md').write_text('x\n')
            for rel, content in (base_files or {}).items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(content)
            git(root, *IDENT, 'add', '-A')
            git(root, *IDENT, 'commit', '-q', '-m', 'base')
            base = git(root, 'rev-parse', 'HEAD').strip()
            if mv:
                (root / mv[1]).parent.mkdir(parents=True, exist_ok=True)
                git(root, 'mv', mv[0], mv[1])
            else:
                (root / 'site/a.md').write_text(text)
            git(root, *IDENT, 'commit', '-qam', 'change')
            os.symlink(SCRIPT.parent.parent, root / '.jury-tools')
            env = {**GIT_ENV, 'LITELLM_URL': 'http://127.0.0.1:%d' % self.server.server_port,
                   'LITELLM_KEY': 'fixture', 'JURY_MODEL': 'm', 'CURL_TIMEOUT_INPUT': '30',
                   'JOB_TIMEOUT_MINUTES': '10', 'DIFF_BUDGET': '60000', 'CLAIMS_MODE': mode,
                   'CLAIMS_URL_HOSTS': 'github.com', 'BASE_REF': 'absent', 'BASE_SHA': base, 'PR_NUMBER': '1',
                   'GITHUB_STEP_SUMMARY': str(root / 'summary.md'), 'RUNNER_TEMP': temp,
                   **(extra_env or {})}
            cmd = ['bash'] + ([flags] if flags else []) + ['-c', self.script]
            proc = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True)
        self.last_out = proc.stdout + proc.stderr
        return proc.returncode, self.state['calls']

    def test_clean_passes_without_model_call(self):
        self.assertEqual(self.run_job('Plain copy.\n'), (0, 0))

    def test_cited_passes_without_model_call(self):
        text = '<!-- claims: proven docs/x.md -->\nWe guarantee it.\n'
        self.assertEqual(self.run_job(text), (0, 0))

    def test_allowlisted_url_cites_and_other_url_does_not(self):
        ok = 'We guarantee it. <!-- claims: proven https://github.com/o/r/pull/1 -->\n'
        self.assertEqual(self.run_job(ok), (0, 0))
        bad = 'We guarantee it. <!-- claims: proven https://evil.example/r -->\n'
        ans = {'classifications': [{'index': 0, 'class': 'unverified'}]}
        self.assertEqual(self.run_job(bad, answer=ans), (1, 1))

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

    def test_malformed_answer_fails_with_message_under_and_without_errexit(self):
        # The runner uses `bash -e {0}`; the job must fail closed with its own
        # message in both shells.
        for flags in ('-e', ''):
            with self.subTest(flags=flags):
                ans = {'classifications': ['proven']}
                rc, calls = self.run_job('We guarantee it.\n', answer=ans, flags=flags)
                self.assertEqual((rc, calls), (2, 1), self.last_out)
                self.assertIn('claims check:', self.last_out)

    def test_duplicate_index_in_the_answer_fails_closed_with_exit_2(self):
        ans = {'classifications': [{'index': 0, 'class': 'proven'},
                                   {'index': 0, 'class': 'guarantee'}]}
        rc, calls = self.run_job('We guarantee it.\n', answer=ans)
        self.assertEqual((rc, calls), (2, 1), self.last_out)
        self.assertIn('duplicate', self.last_out)

    def test_rename_into_public_path_is_checked(self):
        # `git diff` with rename detection hides every line of a moved file.
        ans = {'classifications': [{'index': 0, 'class': 'guarantee'}]}
        rc, calls = self.run_job('', answer=ans, mv=('tmp/o.dat', 'site/n.md'),
                                 base_files={'tmp/o.dat': 'We guarantee it.\n'})
        self.assertEqual((rc, calls), (1, 1), self.last_out)

    def test_claims_paths_input_replaces_the_default_scope(self):
        ans = {'classifications': [{'index': 0, 'class': 'guarantee'}]}
        env = {'CLAIMS_PATHS': 'copy/*.yaml, other/*.txt'}
        rc, calls = self.run_job('', answer=ans, mv=('tmp/o.dat', 'copy/n.yaml'),
                                 base_files={'tmp/o.dat': 'We guarantee it.\n'}, extra_env=env)
        self.assertEqual((rc, calls), (1, 1), self.last_out)
        self.assertEqual(self.run_job('We guarantee it.\n', extra_env=env), (0, 0), self.last_out)

    def test_report_mode_never_fails(self):
        self.assertEqual(self.run_job('We guarantee it.\n', mode='report'), (0, 1))


if __name__ == '__main__':
    unittest.main()
