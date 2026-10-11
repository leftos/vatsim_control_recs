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
