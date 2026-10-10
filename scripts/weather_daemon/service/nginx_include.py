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


def _scan(text: str, start: int, end: int) -> list[tuple[str, int, int]]:
    """Tokenize ``text[start:end]`` into ``(kind, start, end)`` tuples.

    Comments (``#`` to end of line) are skipped, and braces inside comments or
    quoted strings never become tokens, so brace matching ignores both.  Kinds
    are ``word``, ``string``, ``{``, ``}`` and ``;``.
    """
    tokens: list[tuple[str, int, int]] = []
    index = start
    while index < end:
        char = text[index]
        if char in _WHITESPACE:
            index += 1
        elif char == "#":
            newline = text.find("\n", index)
            index = end if newline == -1 else newline + 1
        elif char in "\"'":
            cursor = index + 1
            while cursor < end:
                if text[cursor] == "\\":
                    cursor += 2
                    continue
                if text[cursor] == char:
                    cursor += 1
                    break
                cursor += 1
            tokens.append(("string", index, min(cursor, end)))
            index = min(cursor, end)
        elif char in "{};":
            tokens.append((char, index, index + 1))
            index += 1
        else:
            cursor = index
            while cursor < end and text[cursor] not in _TOKEN_BREAKS:
                cursor += 1
            tokens.append(("word", index, cursor))
            index = cursor
    return tokens


def _find_server_blocks(
    text: str, tokens: list[tuple[str, int, int]]
) -> list[tuple[int, int, int, int]]:
    """Return ``(block_start, body_start, body_end, block_end)`` per top-level server block."""
    blocks: list[tuple[int, int, int, int]] = []
    depth = 0
    index = 0
    while index < len(tokens):
        kind, token_start, token_end = tokens[index]
        if kind == "{":
            depth += 1
        elif kind == "}":
            depth -= 1
        elif kind == "word" and depth == 0 and text[token_start:token_end] == "server":
            nxt = tokens[index + 1] if index + 1 < len(tokens) else None
            if nxt is not None and nxt[0] == "{":
                inner = 0
                cursor = index + 1
                body_end = None
                while cursor < len(tokens):
                    inner_kind, inner_start, inner_end = tokens[cursor]
                    if inner_kind == "{":
                        inner += 1
                    elif inner_kind == "}":
                        inner -= 1
                        if inner == 0:
                            body_end = inner_start
                            blocks.append((token_start, nxt[2], body_end, inner_end))
                            break
                    cursor += 1
                if body_end is None:
                    break
                index = cursor + 1
                continue
        index += 1
    return blocks


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
        kind, token_start, token_end = inner[index]
        if kind == "{":
            depth += 1
        elif kind == "}":
            depth -= 1
        elif kind == "word" and depth == 0:
            name = text[token_start:token_end]
            cursor = index + 1
            inner_depth = 0
            statement_kind = None
            term_start = None
            statement_end = None
            while cursor < len(inner):
                inner_kind, inner_start, inner_end = inner[cursor]
                if inner_kind == "{":
                    if inner_depth == 0:
                        statement_kind = "block"
                        term_start = inner_start
                    inner_depth += 1
                elif inner_kind == "}":
                    inner_depth -= 1
                    if inner_depth == 0 and statement_kind == "block":
                        statement_end = inner_end
                        break
                elif inner_kind == ";" and inner_depth == 0:
                    statement_kind = "simple"
                    term_start = inner_start
                    statement_end = inner_start + 1
                    break
                cursor += 1
            if statement_end is None:
                break
            statements.append(
                {
                    "kind": statement_kind,
                    "name": name,
                    "start": token_start,
                    "end": statement_end,
                    "args": text[token_end:term_start],
                }
            )
            index = cursor + 1
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

    matches = []
    for block in blocks:
        statements = _statements(text, tokens, block[1], block[2])
        has_host = any(
            _HOST_TOKEN in _normalize(statement["args"]).split()
            for statement in statements
            if statement["name"] == "server_name"
        )
        has_tls = any(
            _TLS_PORT in _normalize(statement["args"])
            for statement in statements
            if statement["name"] == "listen"
        )
        if has_host and has_tls:
            matches.append((block, statements))

    described = _describe_blocks(text, tokens, blocks)
    if len(matches) != 1:
        raise ValueError(
            "expected exactly one server block with a server_name containing "
            f"'{_HOST_TOKEN}' and a listen on {_TLS_PORT}, found {len(matches)} "
            f"(of {len(blocks)} server blocks: {described or 'none'})"
        )

    (_block, statements) = matches[0]
    body_end = matches[0][0][2]

    removals = []
    for statement in statements:
        if (
            statement["kind"] == "block"
            and statement["name"] == "location"
            and _normalize(statement["args"]) in _WEATHER_LOCATIONS
        ):
            start = _line_start(text, int(statement["start"]))
            removals.append((start, _line_end(text, int(statement["end"]))))
    removals.sort()

    has_include = any(
        statement["kind"] == "simple"
        and statement["name"] == "include"
        # An absolute path to the same snippet counts as the include existing
        and _normalize(statement["args"]).endswith(INCLUDE_PATH_TOKEN)
        for statement in statements
    )

    insert_at = None
    if not has_include:
        if removals:
            insert_at = removals[0][0]
        else:
            root = next(
                (
                    statement
                    for statement in statements
                    if statement["kind"] == "block"
                    and statement["name"] == "location"
                    and _normalize(statement["args"]) == "/"
                ),
                None,
            )
            anchor = int(root["start"]) if root is not None else body_end
            insert_at = _line_start(text, anchor)

    include = INCLUDE_INDENT + INCLUDE_LINE + _line_ending(text)

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
