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
import json
import re
import sys
from pathlib import Path

DEFAULT_GLOBS = ['*.md', '*.mdx', '*.html', '*.astro', '*.svelte', '*.vue',
                 '*.tsx', '*.jsx']
DEFAULT_DIRS = ['site', 'web', 'docs', 'public', 'legal', 'privacy', 'terms']
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_LINE_CHARS = 2000

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
                body = term
            else:
                body = re.escape(term)
            compiled.append((term, cat, re.compile(r'(?<!\w)(?:' + body + r')(?!\w)', re.IGNORECASE)))
        if not compiled:
            raise ValueError('empty lexicon')
        return compiled
    except (OSError, ValueError, KeyError, TypeError, re.error) as exc:
        raise BadInput('lexicon %s: %s' % (path, exc))


def in_scope(path, globs, dirs):
    parts = path.split('/')
    name = parts[-1]
    if any(fnmatch.fnmatch(name, g) or fnmatch.fnmatch(path, g) for g in globs):
        return True
    return any(d in parts[:-1] for d in dirs)


def parse_added(diff):
    """Yield (file, line_no, text, prev_added_text_or_None) for each added line."""
    seen_header = False
    seen_hunk = False
    in_hunk = False
    cur = None
    new_no = 0
    prev = None
    for raw in diff.split('\n'):
        if raw.startswith('diff --git '):
            seen_header = True
            in_hunk = False
            cur = None
            prev = None
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
            prev = None
            continue
        m = HUNK.match(raw)
        if m:
            seen_hunk = True
            in_hunk = True
            new_no = int(m.group(1))
            prev = None
            continue
        if cur is None or not seen_hunk:
            continue
        if raw.startswith('+'):
            text = raw[1:]
            yield cur, new_no, text, prev
            prev = text
            new_no += 1
        elif raw.startswith(' '):
            new_no += 1
            prev = None
        elif raw.startswith('-') or raw.startswith('\\'):
            continue
    if not (seen_header and seen_hunk):
        raise BadInput('not a unified diff (no file headers and hunks)')


def check(diff, lexicon, globs, dirs):
    hits = []
    for path, line_no, text, prev in parse_added(diff):
        if not in_scope(path, globs, dirs):
            continue
        cited = has_marker(text) or (prev is not None and has_marker(prev))
        scan = MARKER.sub(' ', text)
        for term, cat, rx in lexicon:
            for m in rx.finditer(scan):
                hits.append({'file': path, 'line': line_no,
                             'text': text.strip()[:MAX_LINE_CHARS],
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
