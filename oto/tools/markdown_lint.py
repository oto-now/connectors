"""Markdown pitfalls that render wrong: detection, and a fix where it is safe.

Dependency-free (stdlib only) so it can be reused by the PDF path (pandoc),
the Google Docs/HTML renderer and the Gmail body renderer without pulling in
the `markdown` package.

Currently handles one pitfall: a list glued to the preceding paragraph (no
blank line in between). Python-Markdown/CommonMark won't recognise it as a
list — the bullets render run together. Lines inside a fenced code block
(``` or ~~~) are never a list.

Two uses: `warn_markdown` only warns, the author fixing the source (PDF,
Docs); `fix_glued_lists` inserts the missing blank line, for a renderer whose
author never sees the warning (the Gmail body: an agent writes it, and the
draft would otherwise go out broken).
"""

from __future__ import annotations

import re
import warnings

_LIST_LINE = re.compile(r'^\s*([-*+]|\d+[.)])\s+')  # bullet or ordered item
_FENCE = re.compile(r'^\s*(```|~~~)')


def find_glued_lists(text: str) -> list[int]:
    """1-based line numbers of list items glued to the preceding paragraph.

    Returns the line number of each offending first item; empty if none.
    A line inside a fenced code block, or the fence itself, is never counted.
    """
    lines = text.split('\n')
    hits: list[int] = []
    in_fence = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or i == 0 or not _LIST_LINE.match(line):
            continue
        prev = lines[i - 1]
        if prev.strip() and not _LIST_LINE.match(prev) and not _FENCE.match(prev):
            hits.append(i + 1)
    return hits


def fix_glued_lists(text: str) -> str:
    """The text with a blank line inserted before each list `find_glued_lists` finds.

    Nothing else changes: a text without a glued list comes back identical.
    """
    hits = set(find_glued_lists(text))
    if not hits:
        return text
    out: list[str] = []
    for n, line in enumerate(text.split('\n'), start=1):
        if n in hits:
            out.append('')
        out.append(line)
    return '\n'.join(out)


def lint_markdown(text: str, source: str = '') -> list[str]:
    """Return human-readable warnings for markdown that renders wrong.

    Pure: no side effects. Empty list when nothing is wrong.
    """
    msgs: list[str] = []
    glued = find_glued_lists(text)
    if glued:
        loc = f'{source}: ' if source else ''
        nums = ', '.join(str(n) for n in glued)
        msgs.append(
            f'{loc}list glued to the preceding paragraph (line(s) {nums}): '
            'add a blank line before the bullet, otherwise the rendering runs the bullets together.'
        )
    return msgs


def warn_markdown(text: str, source: str = '') -> list[str]:
    """Emit a ``warnings.warn`` for each pitfall found; return the messages.

    Detection only — the text is never modified. Lets the CLI/MCP surface an
    alert so the author fixes the source.
    """
    msgs = lint_markdown(text, source)
    for msg in msgs:
        warnings.warn(msg, stacklevel=2)
    return msgs
