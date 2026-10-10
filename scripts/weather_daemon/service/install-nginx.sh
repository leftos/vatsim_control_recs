#!/bin/bash
# Install the weather site's nginx snippet and wire it into the leftos.dev TLS
# server block of both site files.
#
# Run as root from the repo install dir (like sync-deps.sh); every deploy calls
# this, so the live config and the snippet cannot drift.  On any failure the
# previous config is restored and left live.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SNIPPET_SRC="$SCRIPT_DIR/nginx-weather.conf"

# Overridable so a test can point them at a temp tree and stub binaries.
NGINX_DIR="${NGINX_DIR:-/etc/nginx}"
NGINX_BIN="${NGINX_BIN:-nginx}"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"
# nginx.conf includes sites-enabled/*, so a backup must never land beside a
# site file: it would be loaded as a second copy of every server block.
BACKUP_DIR="${BACKUP_DIR:-$NGINX_DIR/weather-backup}"

SITE_AVAILABLE="$NGINX_DIR/sites-available/leftos.dev"
SITE_ENABLED="$NGINX_DIR/sites-enabled/leftos.dev"
SNIPPET_DST="$NGINX_DIR/snippets/weather.conf"
SITE_FILES=("$SITE_AVAILABLE" "$SITE_ENABLED")

BACKUP_AVAILABLE="$BACKUP_DIR/sites-available.leftos.dev"
BACKUP_ENABLED="$BACKUP_DIR/sites-enabled.leftos.dev"
BACKUP_SNIPPET="$BACKUP_DIR/snippets.weather.conf"

# Windows test runners often have no python3; the server's system Python is python3
if command -v python3 > /dev/null 2>&1; then
    PYTHON=python3
else
    PYTHON=python
fi

restore_backups() {
    if [ -f "$BACKUP_AVAILABLE" ]; then
        cp "$BACKUP_AVAILABLE" "$SITE_AVAILABLE"
    fi
    if [ -f "$BACKUP_ENABLED" ]; then
        cp "$BACKUP_ENABLED" "$SITE_ENABLED"
    fi
    if [ -f "$BACKUP_SNIPPET" ]; then
        cp "$BACKUP_SNIPPET" "$SNIPPET_DST"
    else
        rm -f "$SNIPPET_DST"
    fi
}

fail_with_old_config() {
    echo "nginx config not updated; the previous config is still live" >&2
    exit 1
}

for file in "${SITE_FILES[@]}"; do
    if [ ! -f "$file" ]; then
        echo "install-nginx.sh: nginx site file not found: $file" >&2
        exit 1
    fi
done

mkdir -p "$NGINX_DIR/snippets" "$BACKUP_DIR"

# A backup must describe this run only, or a failure would restore a stale file
rm -f "$BACKUP_AVAILABLE" "$BACKUP_ENABLED" "$BACKUP_SNIPPET"

cp "$SITE_AVAILABLE" "$BACKUP_AVAILABLE"
cp "$SITE_ENABLED" "$BACKUP_ENABLED"
if [ -f "$SNIPPET_DST" ]; then
    cp "$SNIPPET_DST" "$BACKUP_SNIPPET"
fi

cp "$SNIPPET_SRC" "$SNIPPET_DST"

# The relative path keeps a native python working when the script's own
# directory is an MSYS path a Windows python cannot open
if ! ( cd "$SCRIPT_DIR" && "$PYTHON" ./nginx_include.py "${SITE_FILES[@]}" ); then
    restore_backups
    fail_with_old_config
fi

if ! "$NGINX_BIN" -t; then
    restore_backups
    fail_with_old_config
fi

"$SYSTEMCTL_BIN" reload nginx
echo "weather nginx snippet installed; nginx reloaded"
