#!/bin/bash
# Install the weather daemon's locked Python dependencies on the server.
# Every uv invocation on the server goes through this script.
#
# uv runs as root, but the daemon runs as www-data, so the managed Python lives
# in /opt/uv-python (root's default under /root is unreadable to www-data) and
# the interpreter and venv are made world-readable.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"

export UV_PYTHON_INSTALL_DIR=/opt/uv-python

# Deploys reach this over non-interactive ssh, whose PATH has /usr/local/bin but not
# uv's default ~/.local/bin, so uv is installed there on first use
if ! command -v uv &> /dev/null; then
    echo "Installing uv into /usr/local/bin..."
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
fi

uv sync --frozen --no-dev

# uv may pick a system Python and install none here; the chmod still needs the dir
mkdir -p "$UV_PYTHON_INSTALL_DIR"
chmod -R o+rX "$UV_PYTHON_INSTALL_DIR" .venv
