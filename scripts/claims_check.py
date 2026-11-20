#!/usr/bin/env python3
"""Claims check: flag unverified claims in ADDED public-facing lines of a diff.

usage: claims_check.py [DIFF_FILE | -] [--scope GLOB ...] [--scope-list G,G]
                       [--lexicon PATH] [--repo DIR] [--base REF]
                       [--evidence-paths G,G] [--url-hosts HOST,HOST]

Reads a unified diff (file path, or stdin when omitted or "-"). Checks only
added lines in files that match the scope. Each line is normalised first (HTML
entities, NFKC, format characters, look-alike letters). A marker
`claims: proven <evidence>` cites one claim when its evidence resolves: a path
at HEAD of --repo, a commit in --repo, or an https URL whose host is in
--url-hosts (never fetched). Without --repo or --url-hosts nothing of that
kind resolves, so the marker does not cite.

stdout: {"hits": [{file,line,text,term,category,cited}], "uncited": N}
exit 0 no uncited hits, 1 uncited hits, 2 bad input (fail closed).
Python 3 stdlib only.
"""
import fnmatch
import html
import json
import re
import subprocess
import sys
import unicodedata
from urllib.parse import urlsplit
from pathlib import Path

DEFAULT_GLOBS = ['*.md', '*.mdx', '*.markdown', '*.rst', '*.html', '*.htm',
                 '*.astro', '*.svelte', '*.vue', '*.tsx', '*.jsx', '*.njk', '*.liquid', 'README*']
# Any file under these directories at any depth (so `*.txt` and `*.json`
# there are in scope), plus the locale directories.
DEFAULT_DIRS = ['site', 'web', 'docs', 'public', 'legal', 'privacy', 'terms',
                'pricing', 'landing', 'locales', 'i18n']
# Never in the default scope: licence text is warranty boilerplate.
DEFAULT_EXCLUDE = ['license*', 'licence*', 'copying*', 'unlicense*']
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_LINE_CHARS = 2000
# Whitespace or hyphens between the words of a term: "fully-secure",
# "fully  secure" and "fully secure" are the same phrase.
SEP = r'[\s\-]+'
# Cyrillic and Greek letters that look like Latin ones. Folded to Latin only
# inside a word that mixes scripts.
CONFUSABLE = {
    0x0430: 'a', 0x0435: 'e', 0x043e: 'o', 0x0440: 'p', 0x0441: 'c', 0x0445: 'x',
    0x0443: 'y', 0x0456: 'i', 0x0458: 'j', 0x0455: 's', 0x04bb: 'h', 0x043a: 'k',
    0x03bf: 'o', 0x03b1: 'a', 0x03b9: 'i', 0x03bd: 'v', 0x03c1: 'p', 0x03c5: 'u',
}

HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@')
# The marker token starts the line, or follows whitespace or a comment opener
# (`<!--`, `//`, `#`, `{/*`). So `noclaims: proven x` is not a marker.
MARKER = re.compile(r'(?:^|(?<=\s)|(?<=<!--)|(?<=//)|(?<=#)|(?<=\{/\*))'
                    r'claims:\s*proven\b[ \t]*(\S*)', re.IGNORECASE)
# A marker cites the next line only when its own line holds nothing else,
# optionally wrapped in a comment: `<!-- -->`, `{/* */}`, `//` or `#`.
STANDALONE = re.compile(r'^\s*(?:<!--|\{/\*|//|#)?\s*claims:\s*proven\b[ \t]*\S*'
                        r'(?:\s*(?:-->|\*/\}|\*/))?\s*$', re.IGNORECASE)
URL = re.compile(r'^https?://[^\s<>"\']+$', re.IGNORECASE)
SHA = re.compile(r'^[0-9a-f]{7,40}$', re.IGNORECASE)
PATH = re.compile(r'^(?:\.{0,2}/)?[\w.@+-]+(?:/[\w.@+-]+)+/?$|^[\w@+-]+\.[A-Za-z0-9]{1,8}$')
GIT_TIMEOUT_SECS = 10


class BadInput(Exception):
    pass


def clean_token(token):
    """Strip a closing comment token and trailing punctuation from evidence."""
    while True:
        before = token
        for tail in ('-->', '*/', '}}', '%}'):
            if token.endswith(tail):
                token = token[:-len(tail)]
        token = token.rstrip('.,;)>')
        if token == before:
            return token


class Evidence:
    """Decides whether a marker's evidence resolves. Never fetches a URL.

    - path: a regular, non-empty file (not a symlink, not a directory) at the
      base ref of `repo`, not a file the PR adds, changes or deletes, and, when
      `evidence_globs` is set, matching one of them;
    - sha: a commit that is an ancestor of the base ref
      (git merge-base --is-ancestor <sha> <base>);
    - url: https, no userinfo, no port, host exactly in the allowlist.
    Anything else does not cite, so the check fails closed. Without `base`
    no path or SHA resolves.
    """

    def __init__(self, repo=None, url_hosts=(), base=None, evidence_globs=(), changed=()):
        self.repo = repo
        self.base = base
        self.url_hosts = {h.strip().lower() for h in url_hosts if h.strip()}
        self.evidence_globs = [g.strip() for g in evidence_globs if g.strip()]
        self.changed = set(changed)
        self.cache = {}

    def resolves(self, token):
        token = clean_token(token)
        if not token:
            return False
        if token not in self.cache:
            self.cache[token] = self._resolve(token)
        return self.cache[token]

    def _git(self, *args):
        if not self.repo or not self.base:
            return None
        try:
            r = subprocess.run(['git', '--literal-pathspecs', '-C', self.repo, *args],
                               capture_output=True, timeout=GIT_TIMEOUT_SECS)
        except (OSError, subprocess.SubprocessError):
            return None
        return r if r.returncode == 0 else None

    def _resolve(self, token):
        if URL.match(token):
            return self._url_ok(token)
        if SHA.match(token):
            return self._sha_ok(token)
        if PATH.match(token):
            path = token
            while path.startswith('./'):
                path = path[2:]
            path = path.rstrip('/')
            if not path or path.startswith('/') or path.startswith('-') \
                    or '..' in path.split('/'):
                return False
            return self._path_ok(path)
        return False

    def _sha_ok(self, sha):
        if self.base.startswith('-'):
            return False
        if not self._git('cat-file', '-e', sha + '^{commit}'):
            return False
        full = self._git('rev-parse', '--verify', '-q', sha + '^{commit}')
        if not full:
            return False
        return self._git('merge-base', '--is-ancestor', full.stdout.decode().strip(),
                         self.base) is not None

    def _path_ok(self, path):
        if path in self.changed:
            return False
        if self.evidence_globs and not any(fnmatch.fnmatchcase(path, g)
                                           for g in self.evidence_globs):
            return False
        if self.base.startswith('-'):
            return False
        r = self._git('ls-tree', '-l', self.base, '--', path)
        if not r:
            return False
        rows = r.stdout.decode('utf-8', 'replace').splitlines()
        if len(rows) != 1:
            return False
        meta, _, name = rows[0].partition('\t')
        fields = meta.split()
        if name != path or len(fields) != 4:
            return False
        mode, kind, _, size = fields
        return mode in ('100644', '100755') and kind == 'blob' and size.isdigit() \
            and int(size) > 0

    def _url_ok(self, url):
        try:
            parts = urlsplit(url)
            host = parts.hostname
            port = parts.port
        except ValueError:
            return False
        if parts.scheme != 'https' or not host or port is not None or '@' in parts.netloc:
            return False
        # IP literals and punycode or non-ASCII hosts cite only when listed
        # exactly: the exact compare below is the whole rule.
        return host.lower() in self.url_hosts


def marker_spans(text, evidence):
    """[(start, end, resolves)] for each marker in text."""
    return [(m.start(), m.end(), evidence.resolves(m.group(1)))
            for m in MARKER.finditer(text)]


def load_lexicon(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        compiled = []
        for e in data['entries']:
            term, cat = e['term'], e['category']
            if not isinstance(term, str) or not term or not isinstance(cat, str):
                raise ValueError('entry needs a term and a category')
            if e.get('regex'):
                body = term.replace(' ', SEP)
            else:
                body = SEP.join(re.escape(w) for w in re.split(r'[\s\-]+', term) if w)
            head = r'(?<!\w)' if (e.get('regex') or term[0].isalnum()) else ''
            tail = r'(?!\w)' if (e.get('regex') or term[-1].isalnum()) else ''
            compiled.append((term, cat, re.compile(head + '(?:' + body + ')' + tail, re.IGNORECASE)))
        if not compiled:
            raise ValueError('empty lexicon')
        return compiled
    except (OSError, ValueError, KeyError, TypeError, re.error) as exc:
        raise BadInput('lexicon %s: %s' % (path, exc))


def script_of(ch):
    if not ch.isalpha():
        return None
    word = unicodedata.name(ch, '').split(' ')[0]
    return word if word in ('LATIN', 'CYRILLIC', 'GREEK') else 'OTHER'


def normalise(text):
    """Return (match_text, obfuscation_terms) for one added line.

    Steps: html.unescape, NFKC, drop format characters (zero-width, soft
    hyphen, BOM), fold look-alike letters in mixed-script words, collapse
    whitespace. Mixed-script words and format characters are obfuscation.
    """
    terms = []
    t = unicodedata.normalize('NFKC', html.unescape(text))
    if any(unicodedata.category(c) == 'Cf' for c in html.unescape(text) + t):
        terms.append('zero-width or format character')
    t = ''.join(c for c in t if unicodedata.category(c) != 'Cf')

    def fold(m):
        word = m.group(0)
        scripts = {script_of(c) for c in word} - {None}
        if 'LATIN' in scripts and scripts & {'CYRILLIC', 'GREEK'}:
            terms.append(word)
            return ''.join(CONFUSABLE.get(ord(c), c) for c in word)
        return word

    t = re.sub(r'\w+', fold, t)
    return re.sub(r'\s+', ' ', t), terms


def in_scope(path, globs, dirs, exclude=()):
    """Case-insensitive match of the file name or path against globs, or of a
    parent directory name against dirs. `exclude` globs on the file name win."""
    path = path.lower()
    parts = path.split('/')
    name = parts[-1]
    if any(fnmatch.fnmatchcase(name, g.lower()) for g in exclude):
        return False
    if any(fnmatch.fnmatchcase(name, g.lower()) or fnmatch.fnmatchcase(path, g.lower())
           or fnmatch.fnmatchcase('/' + path, g.lower()) for g in globs):
        return True
    return any(d.lower() in parts[:-1] for d in dirs)


def header_path(raw):
    """Best-effort b-side path of a `diff --git a/X b/Y` line."""
    rest = raw[len('diff --git '):]
    k = rest.rfind(' b/')
    if k < 0:
        return None
    path = rest[k + 3:]
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    return path


def header_a_path(raw):
    """Best-effort a-side path of a `diff --git a/X b/Y` line."""
    rest = raw[len('diff --git '):]
    k = rest.rfind(' b/')
    if k < 0 or not rest.startswith('a/'):
        return None
    path = rest[2:k]
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    return path


def parse_added(diff):
    """Yield ('line', file, line_no, text) per added line,
    ('unreadable', file, reason) per binary change and ('changed', file) per
    path the diff touches (either side).

    Pure renames, mode changes and deletions have no hunks and are valid.
    """
    seen_git = False
    seen_header = False
    seen_hunk = False
    in_hunk = False
    cur = None
    git_path = None
    deleted = False
    reported = False
    new_no = 0
    for raw in diff.split('\n'):
        if raw.startswith('diff --git '):
            seen_git = True
            in_hunk = False
            cur = None
            git_path = header_path(raw)
            for side in (git_path, header_a_path(raw)):
                if side:
                    yield 'changed', side
            deleted = False
            reported = False
            continue
        if not in_hunk and raw.startswith('deleted file mode'):
            deleted = True
            continue
        if not in_hunk and raw.startswith('rename to '):
            git_path = raw[len('rename to '):]
            yield 'changed', git_path
            continue
        if not in_hunk and raw.startswith('rename from '):
            yield 'changed', raw[len('rename from '):]
            continue
        if not in_hunk and (raw.startswith('Binary files ') or raw == 'GIT binary patch'):
            if git_path and not deleted and not reported:
                reported = True
                yield 'unreadable', git_path, 'binary diff'
            continue
        if not in_hunk and raw.startswith('--- '):
            seen_header = True
            continue
        if not in_hunk and raw.startswith('+++ '):
            target = raw[4:].split('\t')[0]
            if target == '/dev/null':
                cur = None
            else:
                cur = target[2:] if target.startswith('b/') else target
            continue
        m = HUNK.match(raw)
        if m:
            seen_hunk = True
            in_hunk = True
            new_no = int(m.group(1))
            continue
        if cur is None or not seen_hunk:
            continue
        if raw.startswith('+'):
            yield 'line', cur, new_no, raw[1:]
            new_no += 1
        elif raw.startswith(' '):
            new_no += 1
    if not (seen_git or (seen_header and seen_hunk)):
        raise BadInput('not a unified diff (no file headers and hunks)')


def blank_spans(text, spans):
    for a, b, _ in spans:
        text = text[:a] + ' ' * (b - a) + text[b:]
    return text


def check(diff, lexicon, globs, dirs, evidence, exclude=()):
    hits = []
    lines = {}
    events = list(parse_added(diff))
    evidence.changed |= {ev[1] for ev in events if ev[0] == 'changed'}
    for ev in events:
        if ev[0] == 'changed':
            continue
        if ev[0] == 'unreadable':
            _, path, reason = ev
            if in_scope(path, globs, dirs, exclude):
                hits.append({'file': path, 'line': 0, 'text': reason,
                             'term': reason, 'category': 'unreadable', 'cited': False})
            continue
        _, path, line_no, text = ev
        if not in_scope(path, globs, dirs, exclude):
            continue
        if '\x00' in text or '\ufffd' in text:
            hits.append({'file': path, 'line': line_no, 'text': 'undecodable text',
                         'term': 'undecodable', 'category': 'unreadable', 'cited': False})
            continue
        norm, odd = normalise(text)
        shown = text.strip()[:MAX_LINE_CHARS]
        spans = marker_spans(norm, evidence)
        found = []
        # Obfuscation is a property of the whole line: it sorts before any marker.
        for term in odd:
            found.append((-1, -1, {'file': path, 'line': line_no, 'text': shown,
                               'term': term, 'category': 'obfuscation', 'cited': False}))
        scan = blank_spans(norm, spans)
        for term, cat, rx in lexicon:
            for m in rx.finditer(scan):
                found.append((m.start(), m.end(), {'file': path, 'line': line_no, 'text': shown,
                                          'term': m.group(0), 'category': cat, 'cited': False}))
        found.sort(key=lambda f: (f[0], f[1]))
        clusters = []
        end = None
        for a, b, h in found:
            if clusters and end is not None and a < end:
                clusters[-1][0].append(h)
                end = max(end, b)
            else:
                clusters.append(([h], a))
                end = b
        lines[(path, line_no)] = (clusters, spans, bool(STANDALONE.match(norm)))
        hits.extend(h for _, _, h in found)
    # A marker cites exactly one claim: one cluster of overlapping hits (for
    # example `99.99%` and `99.99% uptime` are the same words). A marker alone
    # on its line cites the first claim on the next added line; any other
    # marker cites only the nearest claim before it on its own line.
    for (path, line_no), (clusters, spans, standalone) in lines.items():
        for start, _, ok in spans:
            if not ok:
                continue
            if not standalone:
                before = [c for c, pos in clusters if pos < start]
                if before:
                    for h in before[-1]:
                        h['cited'] = True
            else:
                nxt = lines.get((path, line_no + 1))
                if nxt and nxt[0]:
                    for h in nxt[0][0][0]:
                        h['cited'] = True
    return {'hits': hits, 'uncited': sum(1 for h in hits if not h['cited'])}


def parse_args(argv):
    src = None
    scopes = []
    repo = None
    url_hosts = []
    base = None
    evidence_globs = []
    lexicon = Path(__file__).resolve().with_name('claims_lexicon.json')
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ('--scope', '--lexicon', '--repo', '--url-hosts', '--base',
                 '--evidence-paths', '--scope-list'):
            if i + 1 >= len(argv):
                raise BadInput('%s needs a value' % a)
            v = argv[i + 1]
            if a == '--scope':
                scopes.append(v)
            elif a == '--lexicon':
                lexicon = v
            elif a == '--repo':
                repo = v
            elif a == '--scope-list':
                scopes.extend(g.strip() for g in v.split(',') if g.strip())
            elif a == '--base':
                base = v
            elif a == '--evidence-paths':
                evidence_globs = v.split(',')
            else:
                url_hosts = v.split(',')
            i += 2
        elif a.startswith('--'):
            raise BadInput('unknown option %s' % a)
        elif src is None:
            src = a
            i += 1
        else:
            raise BadInput('only one diff input allowed')
    return src, scopes, lexicon, repo, url_hosts, base, evidence_globs


def main(argv):
    try:
        src, scopes, lexicon_path, repo, url_hosts, base, evidence_globs = parse_args(argv)
        try:
            if src in (None, '-'):
                data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
            else:
                with open(src, 'rb') as fh:
                    data = fh.read(MAX_INPUT_BYTES + 1)
        except OSError as exc:
            raise BadInput('cannot read diff: %s' % exc)
        if len(data) > MAX_INPUT_BYTES:
            raise BadInput('diff larger than %d bytes' % MAX_INPUT_BYTES)
        if not data.strip():
            raise BadInput('empty diff')
        diff = data.decode('utf-8', errors='replace')
        lexicon = load_lexicon(lexicon_path)
        globs = scopes if scopes else DEFAULT_GLOBS
        dirs = [] if scopes else DEFAULT_DIRS
        exclude = [] if scopes else DEFAULT_EXCLUDE
        result = check(diff, lexicon, globs, dirs,
                       Evidence(repo, url_hosts, base, evidence_globs), exclude)
    except BadInput as exc:
        sys.stderr.write('claims_check: bad input: %s\n' % exc)
        return 2
    sys.stdout.write(json.dumps(result) + '\n')
    return 1 if result['uncited'] else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
