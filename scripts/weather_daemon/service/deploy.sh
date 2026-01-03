#!/bin/bash
# Deployment Script for VATSIM Weather Daemon
# Run from local machine to deploy updates to the server
#
# Usage: ./deploy.sh
# Automatically resolves IP from leftos.dev domain

set -e

DOMAIN="leftos.dev"
USER="root"
REMOTE_PATH="/opt/vatsim-weather-daemon"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}=== VATSIM Weather Daemon Deployment ===${NC}"

# Resolve IP from domain
echo -e "${YELLOW}Resolving $DOMAIN...${NC}"
if command -v dig &> /dev/null; then
    SERVER_IP=$(dig +short "$DOMAIN" | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -n1)
elif command -v host &> /dev/null; then
    SERVER_IP=$(host "$DOMAIN" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -n1)
elif command -v nslookup &> /dev/null; then
    SERVER_IP=$(nslookup "$DOMAIN" | grep -A1 'Name:' | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -n1)
else
    echo -e "${RED}Error: No DNS lookup tool found (dig, host, or nslookup)${NC}"
    exit 1
fi

if [ -z "$SERVER_IP" ]; then
    echo -e "${RED}Error: Could not resolve $DOMAIN${NC}"
    exit 1
fi

echo -e "${CYAN}Resolved to: $SERVER_IP${NC}"

# Stop the timer and service before deployment
echo -e "${YELLOW}Stopping weather daemon timer and service...${NC}"
ssh "$USER@$SERVER_IP" "systemctl stop weather-daemon.timer 2>/dev/null || true; systemctl stop weather-daemon.service 2>/dev/null || true"
echo -e "${CYAN}Services stopped${NC}"

# Directories to deploy (will sync all files recursively)
DIRECTORIES=(
    "scripts/weather_daemon"
    "backend"
    "ui"
    "airport_disambiguator"
    "data"
)

# Root-level files to deploy
ROOT_FILES=(
    "common.py"
    "requirements.txt"
)

echo -e "${YELLOW}Creating directory structure on remote...${NC}"
ssh "$USER@$SERVER_IP" "mkdir -p $REMOTE_PATH"

echo -e "${YELLOW}Syncing directories...${NC}"
for dir in "${DIRECTORIES[@]}"; do
    if [ -d "$PROJECT_ROOT/$dir" ]; then
        echo -e "${CYAN}  $dir/${NC}"
        rsync -av --delete \
            --exclude '__pycache__' \
            --exclude '*.pyc' \
            --exclude '.git' \
            "$PROJECT_ROOT/$dir/" "$USER@$SERVER_IP:$REMOTE_PATH/$dir/"
    else
        echo -e "${YELLOW}  Warning: $dir not found${NC}"
    fi
done

echo -e "${YELLOW}Uploading root files...${NC}"
for file in "${ROOT_FILES[@]}"; do
    if [ -f "$PROJECT_ROOT/$file" ]; then
        echo -e "${CYAN}  $file${NC}"
        scp "$PROJECT_ROOT/$file" "$USER@$SERVER_IP:$REMOTE_PATH/$file"
    else
        echo -e "${YELLOW}  Warning: $file not found${NC}"
    fi
done

echo -e "${YELLOW}Updating systemd services...${NC}"
ssh "$USER@$SERVER_IP" "cp $REMOTE_PATH/scripts/weather_daemon/service/weather-daemon.service /etc/systemd/system/ && cp $REMOTE_PATH/scripts/weather_daemon/service/weather-daemon.timer /etc/systemd/system/ && systemctl daemon-reload"

echo -e "${YELLOW}Installing Python dependencies...${NC}"
ssh "$USER@$SERVER_IP" "cd $REMOTE_PATH && source .venv/bin/activate && pip install -r requirements.txt"

echo -e "${YELLOW}Clearing caches (except weather)...${NC}"
ssh "$USER@$SERVER_IP" "rm -f $REMOTE_PATH/cache/artcc_boundaries/*.json 2>/dev/null; rm -rf $REMOTE_PATH/cache/simaware_boundaries/* 2>/dev/null; rm -f $REMOTE_PATH/cache/simaware_facilities*.json 2>/dev/null; echo 'Caches cleared'"

echo -e "${YELLOW}Running weather generation...${NC}"
ssh "$USER@$SERVER_IP" "cd $REMOTE_PATH && sudo -u www-data .venv/bin/python -m scripts.weather_daemon.cli --output /var/www/leftos.dev/weather"

# Restart the timer (which will trigger the service on schedule)
echo -e "${YELLOW}Restarting weather daemon timer...${NC}"
ssh "$USER@$SERVER_IP" "systemctl enable weather-daemon.timer && systemctl start weather-daemon.timer"
echo -e "${CYAN}Timer restarted${NC}"

echo ""
echo -e "${GREEN}=== Deployment Complete! ===${NC}"
echo -e "${CYAN}Weather briefings updated at https://leftos.dev/weather/${NC}"
echo -e "${CYAN}Timer active - next run in ~15 minutes${NC}"
