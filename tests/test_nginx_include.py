"""Tests for the weather site's nginx snippet installer.

``scripts/weather_daemon/service/install-nginx.sh`` installs
``nginx-weather.conf`` as ``/etc/nginx/snippets/weather.conf`` and runs
``nginx_include.py`` over both ``leftos.dev`` site files, so the ``leftos.dev``
TLS server block carries one ``include snippets/weather.conf;`` line instead of
the legacy inline weather location blocks.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = REPO_ROOT / "scripts" / "weather_daemon" / "service"

sys.path.insert(0, str(SERVICE_DIR))

import nginx_include  # noqa: E402  (imported after sys.path is set up)

_GIT_ROOT_LEVELS = 3


def _git_bash(start: Path) -> Path | None:
    """Return the bash.exe Git for Windows keeps under ``start``, if any."""
    for _ in range(_GIT_ROOT_LEVELS):
        for relative in ("bin/bash.exe", "usr/bin/bash.exe"):
            candidate = start / relative
            if candidate.is_file():
                return candidate
        start = start.parent
    return None


def _is_wsl_bash(bash: str) -> bool:
    """Return True when ``bash`` is the WSL stub in the Windows ``System32``."""
    system32 = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32")
    resolved = os.path.normcase(str(Path(bash).resolve()))
    return resolved.startswith(os.path.normcase(system32))


def _find_bash() -> str | None:
    """Return a bash these tests can drive; WSL's rejects the MSYS paths."""
    if os.name != "nt":
        return shutil.which("bash")

    git_exe = shutil.which("git")
    if git_exe is not None:
        found = _git_bash(Path(git_exe).resolve().parent)
        if found is not None:
            return str(found)

    bash = shutil.which("bash")
    if bash is None or _is_wsl_bash(bash):
        return None
    return bash


BASH = _find_bash()

INCLUDE_LINE = "    include snippets/weather.conf;"

# The droplet's /etc/nginx/sites-enabled/leftos.dev is a regular file that has
# drifted from sites-available/leftos.dev: only the enabled file carries the
# trailing port-80 server blocks.  The fixture below mirrors both shapes.

TLS_TOP = (
    "server {\n"
    "    server_name leftos.dev www.leftos.dev;\n"
    "\n"
    "    location = /lang-tutor { return 301 /lang-tutor/; }\n"
    "    location ^~ /lang-tutor/ {\n"
    "        alias /var/www/leftos.dev/lang-tutor/;\n"
    "        try_files $uri $uri/ =404;\n"
    "    }\n"
    "\n"
    "    location / {\n"
    "        try_files $uri $uri/ =404;\n"
    "    }\n"
    "\n"
)

TLS_LEGACY_WEATHER = (
    "    # Weather briefings - NO CACHING\n"
    "    location /weather/ {\n"
    "        alias /var/www/leftos.dev/weather/;\n"
    "        index index.html;\n"
    "        try_files $uri $uri/ =404;\n"
    "\n"
    "        autoindex on;\n"
    "        autoindex_exact_size off;\n"
    "\n"
    '        add_header Cache-Control "no-cache, no-store, max-age=0" always;\n'
    '        add_header Pragma "no-cache" always;\n'
    "        etag off;\n"
    "        if_modified_since off;\n"
    "\n"
    "        # A nested block the brace matcher must step over\n"
    '        if ($request_uri ~ "^/weather/[^/]+/$") {\n'
    '            add_header X-Weather-Index "1" always;\n'
    "        }\n"
    "    }\n"
    "\n"
)

TLS_LEGACY_ASSETS = (
    "    # Vendored map assets - the path carries the version, so cache forever.\n"
    "    location ^~ /weather/assets/ {\n"
    "        alias /var/www/leftos.dev/weather/assets/;\n"
    '        add_header Cache-Control "public, max-age=31536000, immutable" always;\n'
    "    }\n"
    "\n"
)

TLS_TAIL = (
    "    location ~* \\.(css|js|png|jpg|jpeg|gif|ico|svg|woff|woff2)$ {\n"
    "        expires 30d;\n"
    '        add_header Cache-Control "public" always;\n'
    "    }\n"
    "\n"
    "    listen [::]:443 ssl ipv6only=on; # managed by Certbot\n"
    "    listen 443 ssl; # managed by Certbot\n"
    "    ssl_certificate /etc/letsencrypt/live/leftos.dev/fullchain.pem;\n"
    "    include /etc/letsencrypt/options-ssl-nginx.conf; # managed by Certbot\n"
    "}\n"
)

TLS_BLOCK = TLS_TOP + TLS_LEGACY_WEATHER + TLS_LEGACY_ASSETS + TLS_TAIL
TLS_BLOCK_WITHOUT_LEGACY = TLS_TOP + TLS_TAIL

PROJECTS_TLS = (
    "server {\n"
    "    listen 443 ssl;\n"
    "    listen [::]:443 ssl;\n"
    "    server_name projects.leftos.dev;\n"
    "\n"
    "    root /var/www/projects.leftos.dev;\n"
    "    location / {\n"
    "        try_files $uri $uri/ =404;\n"
    "    }\n"
    "\n"
    "    include /etc/letsencrypt/options-ssl-nginx.conf; # managed by Certbot\n"
    "}\n"
)

PROJECTS_PORT_80 = (
    "server {\n"
    "    listen 80;\n"
    "    listen [::]:80;\n"
    "    server_name projects.leftos.dev;\n"
    "}\n"
)

LEFTOS_PORT_80 = (
    "server {\n"
    "    listen 80;\n"
    "    listen [::]:80;\n"
    "    server_name leftos.dev www.leftos.dev;\n"
    "\n"
    "    if ($host = www.leftos.dev) {\n"
    "        return 301 https://$host$request_uri;\n"
    "    } # managed by Certbot\n"
    "\n"
    "    if ($host = leftos.dev) {\n"
    "        return 301 https://$host$request_uri;\n"
    "    } # managed by Certbot\n"
    "\n"
    "    return 404; # managed by Certbot\n"
    "}\n"
)

# /etc/nginx/sites-enabled/leftos.dev, the file nginx actually loads.
SITE_ENABLED_TEXT = (
    TLS_BLOCK + "\n" + PROJECTS_TLS + "\n" + PROJECTS_PORT_80 + "\n" + LEFTOS_PORT_80
)
# /etc/nginx/sites-available/leftos.dev, which lacks the port-80 blocks.
SITE_AVAILABLE_TEXT = TLS_BLOCK + "\n" + PROJECTS_TLS


def _run_cli(*args: Path | str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SERVICE_DIR / "nginx_include.py"), *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(SERVICE_DIR),
    )


def test_legacy_weather_blocks_become_one_include_in_the_tls_block():
    result = nginx_include.ensure_weather_include(SITE_ENABLED_TEXT)

    assert result.count("include snippets/weather.conf;") == 1
    assert "location /weather/" not in result
    assert "location ^~ /weather/assets/" not in result

    lines = result.splitlines()
    index = next(
        n for n, line in enumerate(lines) if "include snippets/weather.conf;" in line
    )
    assert lines[index] == INCLUDE_LINE
    # The include lands where the first legacy block began, so the comment that
    # introduced it still reads correctly above it.
    assert lines[index - 1].strip() == "# Weather briefings - NO CACHING"
    # The TLS server block still closes once and the other locations survive
    # byte-for-byte.
    assert "    location / {\n" in result
    assert (
        "    location ~* \\.(css|js|png|jpg|jpeg|gif|ico|svg|woff|woff2)$ {\n" in result
    )


def test_other_server_blocks_are_unchanged():
    result = nginx_include.ensure_weather_include(SITE_ENABLED_TEXT)

    for block in (PROJECTS_TLS, PROJECTS_PORT_80, LEFTOS_PORT_80):
        assert result.count(block) == 1


def test_second_run_changes_nothing():
    once = nginx_include.ensure_weather_include(SITE_ENABLED_TEXT)

    assert nginx_include.ensure_weather_include(once) == once


def test_include_goes_before_location_root_when_no_legacy_block():
    result = nginx_include.ensure_weather_include(TLS_BLOCK_WITHOUT_LEGACY)

    lines = result.splitlines()
    root = lines.index("    location / {")
    assert lines[root - 1] == INCLUDE_LINE


def test_existing_include_is_not_duplicated():
    already_migrated = (
        TLS_TOP
        + INCLUDE_LINE
        + "\n\n"
        + TLS_LEGACY_WEATHER
        + TLS_LEGACY_ASSETS
        + TLS_TAIL
    )

    result = nginx_include.ensure_weather_include(already_migrated)

    assert result.count("include snippets/weather.conf;") == 1
    assert "location /weather/" not in result
    # A file that is already correct is returned untouched.
    assert nginx_include.ensure_weather_include(result) == result

    # An include written with an absolute path is the same include
    absolute = TLS_TOP + "    include /etc/nginx/snippets/weather.conf;\n" + TLS_TAIL
    kept = nginx_include.ensure_weather_include(absolute)
    assert kept.count("snippets/weather.conf") == 1


def test_no_tls_leftos_block_raises_and_names_what_it_found():
    no_leftos_tls = PROJECTS_TLS + "\n" + PROJECTS_PORT_80

    with pytest.raises(ValueError) as excinfo:
        nginx_include.ensure_weather_include(no_leftos_tls)

    message = str(excinfo.value)
    assert "leftos.dev" in message
    assert "projects.leftos.dev" in message

    # Exactly one block must match: a second leftos.dev TLS block is ambiguous.
    ambiguous = TLS_BLOCK + "\n" + TLS_BLOCK
    with pytest.raises(ValueError) as excinfo:
        nginx_include.ensure_weather_include(ambiguous)
    assert "2" in str(excinfo.value)


def test_sites_available_shape_without_port_80_blocks():
    result = nginx_include.ensure_weather_include(SITE_AVAILABLE_TEXT)

    assert result.count("include snippets/weather.conf;") == 1
    assert "location /weather/" not in result
    assert result.count(PROJECTS_TLS) == 1


def test_crlf_and_trailing_newline_are_preserved():
    crlf = SITE_ENABLED_TEXT.replace("\n", "\r\n")

    result = nginx_include.ensure_weather_include(crlf)

    assert result.endswith("\r\n")
    assert "\n" not in result.replace("\r\n", "")
    assert INCLUDE_LINE + "\r\n" in result
    assert "location /weather/" not in result

    assert nginx_include.ensure_weather_include(SITE_ENABLED_TEXT).endswith("\n")


# One-block site files pinning which ``listen`` arguments count as a listen on
# the TLS port and which ``server_name`` arguments name the site host.


def _site_text(listen_statement: str, server_name_statement: str) -> str:
    """Return a site file whose only server block holds the two statements."""
    return (
        "server {\n"
        f"    {listen_statement}\n"
        f"    {server_name_statement}\n"
        "\n"
        "    location /weather/ {\n"
        "        alias /var/www/leftos.dev/weather/;\n"
        "        try_files $uri $uri/ =404;\n"
        "    }\n"
        "}\n"
    )


MATCHING_BLOCKS = [
    ("listen 443 ssl;", "server_name leftos.dev;"),
    ("listen [::]:443 ssl ipv6only=on;", "server_name leftos.dev;"),
    ("listen 0.0.0.0:443;", "server_name leftos.dev;"),
    ("listen *:443;", "server_name leftos.dev;"),
    ("listen 127.0.0.1:443;", "server_name leftos.dev;"),
    ('listen "443" ssl;', "server_name leftos.dev;"),
    ("listen 443 ssl;", 'server_name "leftos.dev";'),
    ("listen 443 ssl;", "server_name 'leftos.dev' www.leftos.dev;"),
]

NON_MATCHING_BLOCKS = [
    ("listen 8443 ssl;", "server_name leftos.dev;"),
    ("listen 4430;", "server_name leftos.dev;"),
    ("listen [::]:8443;", "server_name leftos.dev;"),
    ("listen 80;", "server_name leftos.dev;"),
    ("listen 127.0.0.1:14432;", "server_name leftos.dev;"),
    ("listen ssl 443;", "server_name leftos.dev;"),
    ("listen unix:/run/443.sock;", "server_name leftos.dev;"),
    ("listen 443 ssl;", "server_name www.leftos.dev;"),
    ("listen 443 ssl;", "server_name leftos.dev.example;"),
    ("listen 443 ssl;", "server_name notleftos.dev;"),
    ("listen 443 ssl;", 'server_name "leftos.dev;'),
]


@pytest.mark.parametrize(("listen_statement", "server_name_statement"), MATCHING_BLOCKS)
def test_a_tls_listen_with_a_leftos_name_migrates_the_block(
    listen_statement: str, server_name_statement: str
):
    result = nginx_include.ensure_weather_include(
        _site_text(listen_statement, server_name_statement)
    )

    assert result.count("include snippets/weather.conf;") == 1
    assert "location /weather/" not in result


@pytest.mark.parametrize(
    ("listen_statement", "server_name_statement"), NON_MATCHING_BLOCKS
)
def test_a_block_outside_the_leftos_tls_block_raises(
    listen_statement: str, server_name_statement: str
):
    text = _site_text(listen_statement, server_name_statement)

    with pytest.raises(ValueError) as excinfo:
        nginx_include.ensure_weather_include(text)

    assert "exactly one server block" in str(excinfo.value)


def test_an_8443_leftos_block_beside_a_443_one_edits_only_the_443_one():
    insecure = _site_text("listen 8443 ssl;", "server_name leftos.dev;")
    secure = _site_text("listen 443 ssl;", "server_name leftos.dev;")

    result = nginx_include.ensure_weather_include(insecure + "\n" + secure)

    assert result.count("include snippets/weather.conf;") == 1
    # The 8443 block keeps its legacy weather location, untouched.
    assert result.count("location /weather/") == 1
    assert result.startswith(insecure)
    assert result.index("include snippets/weather.conf;") > len(insecure)


# Characterization of the private scanner, block finder and statement parser.
# Each test pins their current output over an input the public-path tests above
# never reach, so splitting them into helpers cannot change behaviour.


def _scan_all(text: str) -> list[tuple[str, int, int]]:
    return nginx_include._scan(text, 0, len(text))


def _tokens(text: str) -> list[tuple[str, str]]:
    """Return ``(kind, text)`` for every token ``_scan`` finds in ``text``."""
    return [(kind, text[start:end]) for kind, start, end in _scan_all(text)]


def _server_blocks(text: str) -> list[tuple[int, int, int, int]]:
    return nginx_include._find_server_blocks(text, _scan_all(text))


def _statements_of(
    text: str, start: int = 0, end: int | None = None
) -> list[dict[str, object]]:
    return nginx_include._statements(
        text, _scan_all(text), start, len(text) if end is None else end
    )


def test_scan_skips_a_comment_holding_braces_and_semicolons():
    assert _tokens("# { } ;\nword") == [("word", "word")]


def test_scan_skips_an_unterminated_comment_to_the_end_of_the_text():
    assert _tokens("word # { }") == [("word", "word")]


def test_scan_reads_braces_inside_a_double_quoted_string():
    text = 'a "x { } ;" b'

    assert _tokens(text) == [("word", "a"), ("string", '"x { } ;"'), ("word", "b")]


def test_scan_reads_braces_inside_a_single_quoted_string():
    text = "a 'x { } ;' b"

    assert _tokens(text) == [("word", "a"), ("string", "'x { } ;'"), ("word", "b")]


def test_scan_keeps_a_comment_marker_inside_a_string():
    text = 'a "#" b'

    assert _tokens(text) == [("word", "a"), ("string", '"#"'), ("word", "b")]


def test_scan_handles_escaped_quotes_and_backslashes_inside_a_string():
    text = 'a "x \\" { }" b'

    assert _tokens(text) == [("word", "a"), ("string", '"x \\" { }"'), ("word", "b")]


def test_scan_reads_an_unterminated_double_quoted_string_to_the_end():
    text = 'a "unterminated {'

    assert _tokens(text) == [("word", "a"), ("string", '"unterminated {')]


def test_scan_reads_an_unterminated_single_quoted_string_to_the_end():
    text = "a 'unterminated }"

    assert _tokens(text) == [("word", "a"), ("string", "'unterminated }")]


def test_scan_stops_at_a_trailing_backslash_inside_a_string():
    text = 'a "\\'

    assert _tokens(text) == [("word", "a"), ("string", '"\\')]


def test_scan_treats_tabs_and_carriage_returns_as_whitespace():
    text = "a\t{\r\n}\tb"

    assert _tokens(text) == [
        ("word", "a"),
        ("{", "{"),
        ("}", "}"),
        ("word", "b"),
    ]


def test_scan_breaks_words_at_braces_and_semicolons():
    text = "a;b{c}"

    assert _tokens(text) == [
        ("word", "a"),
        (";", ";"),
        ("word", "b"),
        ("{", "{"),
        ("word", "c"),
        ("}", "}"),
    ]


def test_scan_ends_a_word_at_a_hash_that_opens_a_comment():
    assert _tokens("a#b") == [("word", "a")]


def test_scan_ends_a_word_at_a_quote():
    assert _tokens("a'b'") == [("word", "a"), ("string", "'b'")]


def test_scan_starts_from_the_given_offset():
    text = "skip me a;b"

    found = nginx_include._scan(text, 8, len(text))

    assert [(kind, text[start:end]) for kind, start, end in found] == [
        ("word", "a"),
        (";", ";"),
        ("word", "b"),
    ]


def test_find_server_blocks_steps_over_nested_blocks():
    text = "server {\n location / {\n  if ($x) { return 1; }\n }\n}\n"

    blocks = _server_blocks(text)

    assert len(blocks) == 1
    start, body_start, body_end, block_end = blocks[0]
    assert text[start:block_end] == (
        "server {\n location / {\n  if ($x) { return 1; }\n }\n}"
    )
    assert body_start == text.index("{") + 1
    assert text[body_end:block_end] == "}"


def test_find_server_blocks_stops_at_an_unterminated_block():
    assert _server_blocks("server {\n location / {\n}\n") == []


def test_find_server_blocks_ignores_braces_in_comments_and_strings():
    text = 'server {\n    # }\n    add_header X "{ }" always;\n}\n'

    blocks = _server_blocks(text)

    assert len(blocks) == 1
    assert text[blocks[0][2] : blocks[0][3]] == "}"


def test_find_server_blocks_ignores_a_server_word_without_a_block():
    assert _server_blocks("server name;\n") == []


def test_find_server_blocks_ignores_a_trailing_server_word():
    assert _server_blocks("server") == []


def test_find_server_blocks_finds_a_block_after_a_bare_server_word():
    text = "server name;\nserver {\n}\n"

    blocks = _server_blocks(text)

    assert len(blocks) == 1
    assert text[blocks[0][0] : blocks[0][3]] == "server {\n}"


def test_find_server_blocks_ignores_a_stray_closing_brace():
    assert _server_blocks("}\nserver {\n}\n") == []


def test_statements_separate_simple_from_block_statements():
    text = "server_name leftos.dev;\nlocation /weather/ {\n    alias /x/;\n}\n"

    statements = _statements_of(text)

    assert [(s["kind"], s["name"], s["args"]) for s in statements] == [
        ("simple", "server_name", " leftos.dev"),
        ("block", "location", " /weather/ "),
    ]
    assert text[statements[0]["start"] : statements[0]["end"]] == (
        "server_name leftos.dev;"
    )
    assert text[statements[1]["start"] : statements[1]["end"]] == (
        "location /weather/ {\n    alias /x/;\n}"
    )


def test_statements_step_over_a_nested_block():
    text = "location /w/ {\n    if ($x) {\n        return 1;\n    }\n}\n"

    statements = _statements_of(text)

    assert len(statements) == 1
    assert statements[0]["name"] == "location"
    assert text[statements[0]["start"] : statements[0]["end"]] == (
        "location /w/ {\n    if ($x) {\n        return 1;\n    }\n}"
    )


def test_statements_skip_a_word_inside_a_stray_block():
    assert _statements_of("{ inner; }\n") == []


def test_statements_stop_at_an_unterminated_simple_statement():
    assert _statements_of("server_name leftos.dev\n") == []


def test_statements_stop_at_an_unterminated_block_statement():
    assert _statements_of("location /w/ {\n    alias /x/;\n") == []


def test_statements_stop_when_a_block_statement_never_opens():
    assert _statements_of("server }\n") == []


def test_statements_are_limited_to_the_given_range():
    text = "listen 80;\nlisten 443 ssl;\n"
    start = text.index("listen 443")

    statements = _statements_of(text, start, len(text))

    assert [(s["name"], s["args"]) for s in statements] == [("listen", " 443 ssl")]


def test_statements_of_an_empty_range_are_empty():
    assert _statements_of("listen 80;\n", 0, 0) == []


def test_legacy_blocks_in_the_other_order_leave_one_include_at_the_first():
    text = TLS_TOP + TLS_LEGACY_ASSETS + TLS_LEGACY_WEATHER + TLS_TAIL

    result = nginx_include.ensure_weather_include(text)

    assert result.count("include snippets/weather.conf;") == 1
    lines = result.splitlines()
    index = next(
        n for n, line in enumerate(lines) if "include snippets/weather.conf;" in line
    )
    assert lines[index] == INCLUDE_LINE
    assert lines[index - 1].strip() == (
        "# Vendored map assets - the path carries the version, so cache forever."
    )
    assert "location /weather/" not in result
    assert "location ^~ /weather/assets/" not in result
    assert result.count("# Weather briefings - NO CACHING") == 1


def test_two_existing_includes_are_left_exactly_as_they_are():
    text = TLS_TOP + INCLUDE_LINE + "\n" + INCLUDE_LINE + "\n" + TLS_TAIL

    result = nginx_include.ensure_weather_include(text)

    assert result == text
    assert result.count("include snippets/weather.conf;") == 2


def test_include_goes_at_the_end_of_a_block_with_no_location_anchor():
    text = "server {\n    listen 443 ssl;\n    server_name leftos.dev;\n}\n"

    assert nginx_include.ensure_weather_include(text) == (
        "server {\n"
        "    listen 443 ssl;\n"
        "    server_name leftos.dev;\n"
        "    include snippets/weather.conf;\n"
        "}\n"
    )


def test_braces_in_a_comment_and_a_string_do_not_end_the_block_early():
    text = (
        "server {\n"
        "    listen 443 ssl;\n"
        "    server_name leftos.dev;\n"
        '    add_header X-Note "{ not a block }" always;\n'
        "    # a comment with { and } and ;\n"
        "}\n"
    )

    assert nginx_include.ensure_weather_include(text) == (
        "server {\n"
        "    listen 443 ssl;\n"
        "    server_name leftos.dev;\n"
        '    add_header X-Note "{ not a block }" always;\n'
        "    # a comment with { and } and ;\n"
        "    include snippets/weather.conf;\n"
        "}\n"
    )


def test_tab_indentation_and_a_trailing_space_leave_with_the_legacy_block():
    text = (
        "server {\n"
        "\tlisten 443 ssl;\n"
        "\tserver_name leftos.dev;\n"
        "\tlocation /weather/ {\n"
        "\t\talias /var/www/leftos.dev/weather/;\n"
        "\t}\t \n"
        "\tlocation / {\n"
        "\t\ttry_files $uri $uri/ =404;\n"
        "\t}\n"
        "}\n"
    )

    assert nginx_include.ensure_weather_include(text) == (
        "server {\n"
        "\tlisten 443 ssl;\n"
        "\tserver_name leftos.dev;\n"
        "    include snippets/weather.conf;\n"
        "\tlocation / {\n"
        "\t\ttry_files $uri $uri/ =404;\n"
        "\t}\n"
        "}\n"
    )


def test_only_the_leftos_tls_block_among_several_is_edited():
    text = (
        PROJECTS_TLS
        + "\n"
        + PROJECTS_PORT_80
        + "\n"
        + TLS_BLOCK
        + "\n"
        + LEFTOS_PORT_80
    )

    result = nginx_include.ensure_weather_include(text)

    assert result.count("include snippets/weather.conf;") == 1
    for block in (PROJECTS_TLS, PROJECTS_PORT_80, LEFTOS_PORT_80):
        assert result.count(block) == 1
    assert "location /weather/" not in result


def test_error_message_names_a_block_with_neither_name_nor_listen():
    with pytest.raises(ValueError) as excinfo:
        nginx_include.ensure_weather_include("server {\n}\n")

    message = str(excinfo.value)
    assert "found 0" in message
    assert "of 1 server blocks" in message
    assert "server_name[none] listen[none]" in message


def test_an_absolute_include_drops_the_legacy_blocks_without_adding_a_line():
    text = (
        TLS_TOP
        + "    include /etc/nginx/snippets/weather.conf;\n"
        + TLS_LEGACY_WEATHER
        + TLS_LEGACY_ASSETS
        + TLS_TAIL
    )

    result = nginx_include.ensure_weather_include(text)

    assert result.count("snippets/weather.conf") == 1
    assert "include snippets/weather.conf;" not in result
    assert "location /weather/" not in result
    assert "location ^~ /weather/assets/" not in result


def test_a_file_without_a_trailing_newline_stays_without_one():
    text = "server {\n    listen 443 ssl;\n    server_name leftos.dev;\n}"

    result = nginx_include.ensure_weather_include(text)

    assert result == (
        "server {\n"
        "    listen 443 ssl;\n"
        "    server_name leftos.dev;\n"
        "    include snippets/weather.conf;\n"
        "}"
    )
    assert not result.endswith("\n")


def test_crlf_line_endings_without_a_legacy_block_keep_the_include_crlf():
    text = TLS_BLOCK_WITHOUT_LEGACY.replace("\n", "\r\n")

    result = nginx_include.ensure_weather_include(text)

    assert INCLUDE_LINE + "\r\n" in result
    assert "\n" not in result.replace("\r\n", "")


def test_cli_rewrites_changed_files_only(tmp_path: Path):
    changed = tmp_path / "enabled.conf"
    changed.write_text(SITE_ENABLED_TEXT, encoding="utf-8", newline="")
    unchanged = tmp_path / "available.conf"
    unchanged.write_text(SITE_AVAILABLE_TEXT, encoding="utf-8", newline="")
    migrated = nginx_include.ensure_weather_include(SITE_AVAILABLE_TEXT)
    unchanged.write_text(migrated, encoding="utf-8", newline="")
    old_mtime = 1_000_000_000.0
    os.utime(unchanged, (old_mtime, old_mtime))

    done = _run_cli(changed, unchanged)

    assert done.returncode == 0, done.stderr
    assert changed.read_text(encoding="utf-8", newline="") != SITE_ENABLED_TEXT
    assert "location /weather/" not in changed.read_text(encoding="utf-8")
    assert "updated" in done.stdout
    assert "unchanged" in done.stdout
    assert unchanged.stat().st_mtime == old_mtime
    assert unchanged.read_text(encoding="utf-8", newline="") == migrated

    bad = tmp_path / "bad.conf"
    bad.write_text(PROJECTS_TLS, encoding="utf-8", newline="")

    failed = _run_cli(bad)

    assert failed.returncode == 1
    assert "leftos.dev" in failed.stderr
    assert bad.read_text(encoding="utf-8", newline="") == PROJECTS_TLS


def _to_posix(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def _write_tree_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def _make_stub(path: Path, exit_code: int, record: Path) -> None:
    path.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{_to_posix(record)}"\nexit {exit_code}\n',
        encoding="utf-8",
        newline="\n",
    )
    os.chmod(path, 0o755)
    if os.name == "nt":
        # Python on Windows cannot set the MSYS executable flag
        subprocess.run(
            [BASH, "-c", 'chmod +x "$1"', "_", _to_posix(path)],
            check=True,
        )


def _run_install_script(nginx_dir: Path, nginx_bin: Path, systemctl_bin: Path):
    env = dict(os.environ)
    env["NGINX_DIR"] = _to_posix(nginx_dir)
    env["NGINX_BIN"] = _to_posix(nginx_bin)
    env["SYSTEMCTL_BIN"] = _to_posix(systemctl_bin)
    return subprocess.run(
        [BASH, _to_posix(SERVICE_DIR / "install-nginx.sh")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def _nginx_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build a temp nginx tree whose site files look like the droplet's."""
    nginx_dir = tmp_path / "nginx"
    _write_tree_file(nginx_dir / "sites-available" / "leftos.dev", SITE_AVAILABLE_TEXT)
    _write_tree_file(nginx_dir / "sites-enabled" / "leftos.dev", SITE_ENABLED_TEXT)
    (nginx_dir / "snippets").mkdir(parents=True)
    return nginx_dir, nginx_dir / "snippets" / "weather.conf", tmp_path


@pytest.mark.skipif(
    BASH is None, reason="Git Bash (or a non-WSL bash) is not available"
)
def test_install_script_restores_backups_when_nginx_test_fails(tmp_path: Path):
    nginx_dir, snippet_dst, root = _nginx_tree(tmp_path)
    nginx_stub = tmp_path / "nginx-stub"
    systemctl_stub = tmp_path / "systemctl-stub"
    nginx_record = tmp_path / "nginx-calls.txt"
    _make_stub(nginx_stub, 1, nginx_record)
    _make_stub(systemctl_stub, 0, tmp_path / "systemctl-calls.txt")

    done = _run_install_script(nginx_dir, nginx_stub, systemctl_stub)

    assert done.returncode != 0
    assert "previous config is still live" in done.stdout + done.stderr
    assert (nginx_dir / "sites-available" / "leftos.dev").read_text(
        encoding="utf-8", newline=""
    ) == SITE_AVAILABLE_TEXT
    assert (nginx_dir / "sites-enabled" / "leftos.dev").read_text(
        encoding="utf-8", newline=""
    ) == SITE_ENABLED_TEXT
    # nginx.conf includes sites-enabled/*, so no backup may live there
    assert sorted(path.name for path in (nginx_dir / "sites-enabled").iterdir()) == [
        "leftos.dev"
    ]
    # There was no previous snippet to restore, so the new one is removed
    assert not snippet_dst.exists()
    assert "-t" in nginx_record.read_text(encoding="utf-8")
    assert not (root / "systemctl-calls.txt").exists()


@pytest.mark.skipif(
    BASH is None, reason="Git Bash (or a non-WSL bash) is not available"
)
def test_install_script_reloads_on_success(tmp_path: Path):
    nginx_dir, snippet_dst, root = _nginx_tree(tmp_path)
    nginx_stub = tmp_path / "nginx-stub"
    systemctl_stub = tmp_path / "systemctl-stub"
    _make_stub(nginx_stub, 0, tmp_path / "nginx-calls.txt")
    systemctl_record = tmp_path / "systemctl-calls.txt"
    _make_stub(systemctl_stub, 0, systemctl_record)

    done = _run_install_script(nginx_dir, nginx_stub, systemctl_stub)

    assert done.returncode == 0, done.stderr
    assert snippet_dst.read_text(encoding="utf-8", newline="") == (
        SERVICE_DIR / "nginx-weather.conf"
    ).read_text(encoding="utf-8", newline="")
    enabled = (nginx_dir / "sites-enabled" / "leftos.dev").read_text(
        encoding="utf-8", newline=""
    )
    available = (nginx_dir / "sites-available" / "leftos.dev").read_text(
        encoding="utf-8", newline=""
    )
    assert enabled.count("include snippets/weather.conf;") == 1
    assert available.count("include snippets/weather.conf;") == 1
    assert "location /weather/" not in enabled
    assert (nginx_dir / "weather-backup" / "sites-enabled.leftos.dev").read_text(
        encoding="utf-8", newline=""
    ) == SITE_ENABLED_TEXT
    assert (nginx_dir / "weather-backup" / "sites-available.leftos.dev").read_text(
        encoding="utf-8", newline=""
    ) == SITE_AVAILABLE_TEXT
    # nginx.conf includes sites-enabled/*, so no backup may live there
    assert sorted(path.name for path in (nginx_dir / "sites-enabled").iterdir()) == [
        "leftos.dev"
    ]
    assert "reload nginx" in systemctl_record.read_text(encoding="utf-8")
    assert root.exists()


@pytest.mark.skipif(
    BASH is None, reason="Git Bash (or a non-WSL bash) is not available"
)
def test_stale_snippet_backup_is_not_restored(tmp_path: Path):
    nginx_dir, snippet_dst, _root = _nginx_tree(tmp_path)
    _write_tree_file(
        nginx_dir / "weather-backup" / "snippets.weather.conf",
        "# left by an earlier run\n",
    )
    nginx_stub = tmp_path / "nginx-stub"
    systemctl_stub = tmp_path / "systemctl-stub"
    _make_stub(nginx_stub, 1, tmp_path / "nginx-calls.txt")
    _make_stub(systemctl_stub, 0, tmp_path / "systemctl-calls.txt")

    done = _run_install_script(nginx_dir, nginx_stub, systemctl_stub)

    assert done.returncode != 0
    # The stale backup must not be mistaken for this run's previous snippet
    assert not snippet_dst.exists()
