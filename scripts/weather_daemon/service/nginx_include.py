"""Rewrite an nginx site file so the weather site is served by a snippet include.

``install-nginx.sh`` installs ``nginx-weather.conf`` as
``/etc/nginx/snippets/weather.conf`` and calls this module on
``/etc/nginx/sites-available/leftos.dev`` and
``/etc/nginx/sites-enabled/leftos.dev``.  It finds the ``leftos.dev`` TLS
server block in each file, removes the legacy inline ``location /weather/`` and
``location ^~ /weather/assets/`` blocks, and leaves exactly one ``include
snippets/weather.conf;`` line where the first of them began.

Run as ``python3 nginx_include.py FILE...``; it rewrites only the files whose
text changes and prints one line per file.
"""

from __future__ import annotations

import os
import sys
import tempfile

INCLUDE_LINE = "include snippets/weather.conf;"
INCLUDE_PATH_TOKEN = "snippets/weather.conf"
INCLUDE_INDENT = " " * 4

_HOST_TOKEN = "leftos.dev"
_TLS_PORT = "443"

# Normalized arguments of the two legacy location blocks this module removes.
_WEATHER_LOCATIONS = frozenset({"/weather/", "^~ /weather/assets/"})

_WHITESPACE = " \t\r\n"
_TOKEN_BREAKS = " \t\r\n{};\"'#"


def _skip_comment(text: str, index: int, end: int) -> int:
    """Return the index just past the comment that starts at ``index``."""
    newline = text.find("\n", index)
    return end if newline == -1 else newline + 1


def _scan_string(text: str, index: int, end: int) -> tuple[tuple[str, int, int], int]:
    """Return the string token opening at ``index`` and the index after it."""
    quote = text[index]
    cursor = index + 1
    while cursor < end:
        if text[cursor] == "\\":
            cursor += 2
        elif text[cursor] == quote:
            cursor += 1
            break
        else:
            cursor += 1
    stop = min(cursor, end)
    return ("string", index, stop), stop


def _scan_word(text: str, index: int, end: int) -> tuple[tuple[str, int, int], int]:
    """Return the word token starting at ``index`` and the index after it."""
    cursor = index
    while cursor < end and text[cursor] not in _TOKEN_BREAKS:
        cursor += 1
    return ("word", index, cursor), cursor


def _scan_one(
    text: str, index: int, end: int
) -> tuple[tuple[str, int, int] | None, int]:
    """Return the token at ``index`` and the next index, or None when it is skipped.

    Whitespace is stepped over and a ``#`` runs to the end of its line.
    """
    char = text[index]
    if char in _WHITESPACE:
        return None, index + 1
    if char == "#":
        return None, _skip_comment(text, index, end)
    if char in "\"'":
        return _scan_string(text, index, end)
    if char in "{};":
        return (char, index, index + 1), index + 1
    return _scan_word(text, index, end)


def _scan(text: str, start: int, end: int) -> list[tuple[str, int, int]]:
    """Tokenize ``text[start:end]`` into ``(kind, start, end)`` tuples.

    Comments (``#`` to end of line) are skipped, and braces inside comments or
    quoted strings never become tokens, so brace matching ignores both.  Kinds
    are ``word``, ``string``, ``{``, ``}`` and ``;``.
    """
    tokens: list[tuple[str, int, int]] = []
    index = start
    while index < end:
        token, index = _scan_one(text, index, end)
        if token is not None:
            tokens.append(token)
    return tokens


def _is_server_block_start(
    text: str, tokens: list[tuple[str, int, int]], index: int, depth: int
) -> bool:
    """Return True when ``tokens[index]`` opens a top-level ``server`` block."""
    if depth != 0:
        return False
    kind, start, end = tokens[index]
    if kind != "word" or text[start:end] != "server":
        return False
    return index + 1 < len(tokens) and tokens[index + 1][0] == "{"


def _server_block_span(
    tokens: list[tuple[str, int, int]], index: int
) -> tuple[tuple[int, int, int, int], int] | None:
    """Return the ``server`` block opening after ``index`` and the next token index.

    ``tokens[index + 1]`` must be that block's opening ``{``, which the caller
    has already checked with ``_is_server_block_start``.  The block is
    ``(block_start, body_start, body_end, block_end)``; None means its closing
    ``}`` never arrived.
    """
    nxt = tokens[index + 1]
    inner = 0
    cursor = index + 1
    while cursor < len(tokens):
        kind, start, end = tokens[cursor]
        if kind == "{":
            inner += 1
        elif kind == "}":
            inner -= 1
            if inner == 0:
                return (tokens[index][1], nxt[2], start, end), cursor + 1
        cursor += 1
    return None


def _find_server_blocks(
    text: str, tokens: list[tuple[str, int, int]]
) -> list[tuple[int, int, int, int]]:
    """Return ``(block_start, body_start, body_end, block_end)`` per top-level server block."""
    blocks: list[tuple[int, int, int, int]] = []
    depth = 0
    index = 0
    while index < len(tokens):
        kind = tokens[index][0]
        if kind == "{":
            depth += 1
        elif kind == "}":
            depth -= 1
        elif _is_server_block_start(text, tokens, index, depth):
            found = _server_block_span(tokens, index)
            if found is None:
                break
            block, index = found
            blocks.append(block)
            continue
        index += 1
    return blocks


def _find_terminator(
    inner: list[tuple[str, int, int]], index: int
) -> tuple[str, int, int, int] | None:
    """Return ``(kind, term_start, statement_end, next_index)`` for the statement at ``index``.

    ``kind`` is ``simple`` for a statement ended by ``;`` or ``block`` for one
    whose ``{`` is closed before any ``;``; None when neither arrives.
    """
    block_start = None
    inner_depth = 0
    cursor = index + 1
    while cursor < len(inner):
        kind, token_start, token_end = inner[cursor]
        if kind == "{":
            if inner_depth == 0:
                block_start = token_start
            inner_depth += 1
        elif kind == "}":
            inner_depth -= 1
            if inner_depth == 0 and block_start is not None:
                return "block", block_start, token_end, cursor + 1
        elif kind == ";" and inner_depth == 0:
            return "simple", token_start, token_start + 1, cursor + 1
        cursor += 1
    return None


def _statement_at(
    text: str, inner: list[tuple[str, int, int]], index: int
) -> tuple[dict[str, object], int] | None:
    """Return the statement starting at ``index`` and the index after it, or None."""
    terminator = _find_terminator(inner, index)
    if terminator is None:
        return None
    kind, term_start, statement_end, cursor = terminator
    return (
        {
            "kind": kind,
            "name": text[inner[index][1] : inner[index][2]],
            "start": inner[index][1],
            "end": statement_end,
            "args": text[inner[index][2] : term_start],
        },
        cursor,
    )


def _statements(
    text: str, tokens: list[tuple[str, int, int]], start: int, end: int
) -> list[dict[str, object]]:
    """Return the top-level statements of ``text[start:end]``.

    Each entry is ``{"kind", "name", "start", "end", "args"}`` where ``kind`` is
    ``simple`` (terminated by ``;``) or ``block`` (terminated by its closing
    ``}``), and ``args`` is the raw text between the name and the terminator.
    """
    inner = [token for token in tokens if token[1] >= start and token[2] <= end]
    statements: list[dict[str, object]] = []
    depth = 0
    index = 0
    while index < len(inner):
        kind = inner[index][0]
        if kind == "{":
            depth += 1
        elif kind == "}":
            depth -= 1
        elif kind == "word" and depth == 0:
            found = _statement_at(text, inner, index)
            if found is None:
                break
            statement, index = found
            statements.append(statement)
            continue
        index += 1
    return statements


def _normalize(args: object) -> str:
    return " ".join(str(args).split())


def _describe_blocks(
    text: str,
    tokens: list[tuple[str, int, int]],
    blocks: list[tuple[int, int, int, int]],
) -> str:
    """Describe every server block found, for the ValueError message."""
    described = []
    for _block_start, body_start, body_end, _block_end in blocks:
        statements = _statements(text, tokens, body_start, body_end)
        names = " ".join(
            _normalize(statement["args"])
            for statement in statements
            if statement["name"] == "server_name"
        )
        listens = " ".join(
            _normalize(statement["args"])
            for statement in statements
            if statement["name"] == "listen"
        )
        described.append(f"server_name[{names or 'none'}] listen[{listens or 'none'}]")
    return ", ".join(described)


def _line_start(text: str, index: int) -> int:
    return text.rfind("\n", 0, index) + 1


def _line_end(text: str, index: int) -> int:
    """Extend ``index`` past the trailing whitespace of its line, if any."""
    cursor = index
    length = len(text)
    while cursor < length and text[cursor] in " \t":
        cursor += 1
    if cursor < length and text[cursor] == "\r":
        cursor += 1
    if cursor < length and text[cursor] == "\n":
        cursor += 1
    return cursor


def _line_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _is_weather_location(statement: dict[str, object]) -> bool:
    """Return True for a legacy ``location`` block serving the weather site."""
    return (
        statement["kind"] == "block"
        and statement["name"] == "location"
        and _normalize(statement["args"]) in _WEATHER_LOCATIONS
    )


def _is_root_location(statement: dict[str, object]) -> bool:
    """Return True for the site's ``location /`` block."""
    return (
        statement["kind"] == "block"
        and statement["name"] == "location"
        and _normalize(statement["args"]) == "/"
    )


def _weather_removals(
    text: str, statements: list[dict[str, object]]
) -> list[tuple[int, int]]:
    """Return the ``(start, end)`` spans of the legacy weather location blocks."""
    removals: list[tuple[int, int]] = []
    for statement in statements:
        if _is_weather_location(statement):
            start = _line_start(text, int(statement["start"]))
            removals.append((start, _line_end(text, int(statement["end"]))))
    removals.sort()
    return removals


def _has_include(statements: list[dict[str, object]]) -> bool:
    """Return True when the block already includes the weather snippet."""
    return any(
        statement["kind"] == "simple"
        and statement["name"] == "include"
        # An absolute path to the same snippet counts as the include existing
        and _normalize(statement["args"]).endswith(INCLUDE_PATH_TOKEN)
        for statement in statements
    )


def _include_anchor(statements: list[dict[str, object]], body_end: int) -> int:
    """Return the offset of the ``location /`` block, or the end of the body."""
    for statement in statements:
        if _is_root_location(statement):
            return int(statement["start"])
    return body_end


def _include_position(
    text: str,
    statements: list[dict[str, object]],
    removals: list[tuple[int, int]],
    body_end: int,
) -> int | None:
    """Return where the include line goes, or None when the block already has it."""
    if _has_include(statements):
        return None
    if removals:
        return removals[0][0]
    return _line_start(text, _include_anchor(statements, body_end))


def _block_has_host(statements: list[dict[str, object]]) -> bool:
    """Return True when the block's ``server_name`` names the site host."""
    return any(
        _HOST_TOKEN in _normalize(statement["args"]).split()
        for statement in statements
        if statement["name"] == "server_name"
    )


def _block_has_tls(statements: list[dict[str, object]]) -> bool:
    """Return True when the block listens on the TLS port."""
    return any(
        _TLS_PORT in _normalize(statement["args"])
        for statement in statements
        if statement["name"] == "listen"
    )


def _matching_blocks(
    text: str,
    tokens: list[tuple[str, int, int]],
    blocks: list[tuple[int, int, int, int]],
) -> list[tuple[tuple[int, int, int, int], list[dict[str, object]]]]:
    """Return the blocks serving the host over TLS, with their statements."""
    matches = []
    for block in blocks:
        statements = _statements(text, tokens, block[1], block[2])
        if _block_has_host(statements) and _block_has_tls(statements):
            matches.append((block, statements))
    return matches


def _splice(
    text: str, removals: list[tuple[int, int]], insert_at: int | None, include: str
) -> str:
    """Return ``text`` with every removal span dropped and ``include`` placed once."""
    pieces = []
    position = 0
    for start, end in removals:
        if insert_at is not None and start >= insert_at:
            pieces.append(text[position:insert_at])
            pieces.append(include)
            position = insert_at
            insert_at = None
        pieces.append(text[position:start])
        position = end
    if insert_at is not None:
        pieces.append(text[position:insert_at])
        pieces.append(include)
        position = insert_at
    pieces.append(text[position:])
    return "".join(pieces)


def ensure_weather_include(text: str) -> str:
    """Return ``text`` with the weather snippet included by the leftos.dev TLS block.

    Exactly one top-level ``server`` block must have a ``server_name``
    containing the token ``leftos.dev`` and a ``listen`` on 443; anything else
    raises ``ValueError``.  The legacy inline weather locations in that block
    are removed, and a single ``include snippets/weather.conf;`` line takes the
    place of the first one.  The function is idempotent and preserves the
    file's line endings and trailing newline.
    """
    tokens = _scan(text, 0, len(text))
    blocks = _find_server_blocks(text, tokens)
    matches = _matching_blocks(text, tokens, blocks)

    if len(matches) != 1:
        described = _describe_blocks(text, tokens, blocks)
        raise ValueError(
            "expected exactly one server block with a server_name containing "
            f"'{_HOST_TOKEN}' and a listen on {_TLS_PORT}, found {len(matches)} "
            f"(of {len(blocks)} server blocks: {described or 'none'})"
        )

    block, statements = matches[0]
    removals = _weather_removals(text, statements)
    insert_at = _include_position(text, statements, removals, block[2])
    include = INCLUDE_INDENT + INCLUDE_LINE + _line_ending(text)
    return _splice(text, removals, insert_at, include)


def _read_file(path: str) -> str:
    # newline="" keeps the file's own line endings instead of translating them
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_file_atomically(path: str, text: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    descriptor, temp_path = tempfile.mkstemp(
        dir=directory, prefix=".nginx_include.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temp_path, path)
    except OSError:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def main(argv: list[str]) -> int:
    paths = argv[1:]
    if not paths:
        print("usage: nginx_include.py FILE...", file=sys.stderr)
        return 2
    for path in paths:
        try:
            text = _read_file(path)
            updated = ensure_weather_include(text)
        except (OSError, ValueError) as error:
            print(f"{path}: {error}", file=sys.stderr)
            return 1
        if updated == text:
            print(f"{path}: unchanged")
            continue
        _write_file_atomically(path, updated)
        print(f"{path}: updated")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
