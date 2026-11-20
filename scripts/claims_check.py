#!/usr/bin/env python3
"""Claims check: flag unverified claims in ADDED public-facing lines of a diff.

usage: claims_check.py [DIFF_FILE | -] [--scope GLOB ...] [--lexicon PATH]

Reads a unified diff (file path, or stdin when omitted or "-"). Checks only
added lines in files that match the scope. A hit is cited when the same line
or the added line right before it carries `claims: proven <evidence>`, where
evidence is a URL, a repo path or a commit SHA.

stdout: {"hits": [{file,line,text,term,category,cited}], "uncited": N}
exit 0 no uncited hits, 1 uncited hits, 2 bad input (fail closed).
Python 3 stdlib only.
"""
import fnmatch
import html
import json
import re
import sys
import unicodedata
from pathlib import Path

DEFAULT_GLOBS = ['*.md', '*.mdx', '*.markdown', '*.rst', '*.html', '*.htm',
                 '*.astro', '*.svelte', '*.vue', '*.tsx', '*.jsx', 'README*']
DEFAULT_DIRS = ['site', 'web', 'docs', 'public', 'legal', 'privacy', 'terms',
                'pricing', 'landing']
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
MARKER = re.compile(r'claims:\s*proven\b[ \t]*(\S*)', re.IGNORECASE)
URL = re.compile(r'^https?://[^\s<>"\']+$', re.IGNORECASE)
SHA = re.compile(r'^[0-9a-f]{7,40}$', re.IGNORECASE)
PATH = re.compile(r'^(?:\.{0,2}/)?[\w.@+-]+(?:/[\w.@+-]+)+/?$|^[\w@+-]+\.[A-Za-z0-9]{1,8}$')


class BadInput(Exception):
    pass


def evidence_ok(token):
    token = token.rstrip('.,;)>')
    for tail in ('-->', '*/', '}}', '%}'):
        if token.endswith(tail):
            token = token[:-len(tail)]
    if not token:
        return False
    return bool(URL.match(token) or SHA.match(token) or PATH.match(token))


def has_marker(text):
    return any(evidence_ok(m.group(1)) for m in MARKER.finditer(text))


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


def in_scope(path, globs, dirs):
    """Case-insensitive match of the file name or path against globs, or of a
    parent directory name against dirs."""
    path = path.lower()
    parts = path.split('/')
    name = parts[-1]
    if any(fnmatch.fnmatchcase(name, g.lower()) or fnmatch.fnmatchcase(path, g.lower())
           for g in globs):
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


def parse_added(diff):
    """Yield ('line', file, line_no, text) per added line and
    ('unreadable', file, reason) per binary change.

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
            deleted = False
            reported = False
            continue
        if not in_hunk and raw.startswith('deleted file mode'):
            deleted = True
            continue
        if not in_hunk and raw.startswith('rename to '):
            git_path = raw[len('rename to '):]
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


def check(diff, lexicon, globs, dirs):
    hits = []
    prev_key = prev_text = None
    for ev in parse_added(diff):
        if ev[0] == 'unreadable':
            _, path, reason = ev
            if in_scope(path, globs, dirs):
                hits.append({'file': path, 'line': 0, 'text': reason,
                             'term': reason, 'category': 'unreadable', 'cited': False})
            continue
        _, path, line_no, text = ev
        prev = prev_text if prev_key == (path, line_no - 1) else None
        prev_key, prev_text = (path, line_no), text
        if not in_scope(path, globs, dirs):
            continue
        if '\x00' in text or '\ufffd' in text:
            hits.append({'file': path, 'line': line_no, 'text': 'undecodable text',
                         'term': 'undecodable', 'category': 'unreadable', 'cited': False})
            continue
        cited = has_marker(text) or (prev is not None and has_marker(prev))
        norm, odd = normalise(text)
        shown = text.strip()[:MAX_LINE_CHARS]
        for term in odd:
            hits.append({'file': path, 'line': line_no, 'text': shown,
                         'term': term, 'category': 'obfuscation', 'cited': cited})
        scan = MARKER.sub(' ', norm)
        for term, cat, rx in lexicon:
            for m in rx.finditer(scan):
                hits.append({'file': path, 'line': line_no, 'text': shown,
                             'term': m.group(0), 'category': cat, 'cited': cited})
    return {'hits': hits, 'uncited': sum(1 for h in hits if not h['cited'])}


def parse_args(argv):
    src = None
    scopes = []
    lexicon = Path(__file__).resolve().with_name('claims_lexicon.json')
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ('--scope', '--lexicon'):
            if i + 1 >= len(argv):
                raise BadInput('%s needs a value' % a)
            if a == '--scope':
                scopes.append(argv[i + 1])
            else:
                lexicon = argv[i + 1]
            i += 2
        elif a.startswith('--'):
            raise BadInput('unknown option %s' % a)
        elif src is None:
            src = a
            i += 1
        else:
            raise BadInput('only one diff input allowed')
    return src, scopes, lexicon


def main(argv):
    try:
        src, scopes, lexicon_path = parse_args(argv)
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
        result = check(diff, lexicon, globs, dirs)
    except BadInput as exc:
        sys.stderr.write('claims_check: bad input: %s\n' % exc)
        return 2
    sys.stdout.write(json.dumps(result) + '\n')
    return 1 if result['uncited'] else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
