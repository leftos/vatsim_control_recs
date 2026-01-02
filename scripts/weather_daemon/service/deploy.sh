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

# Files to deploy (relative to project root)
FILES=(
    "scripts/weather_daemon/__init__.py"
    "scripts/weather_daemon/cli.py"
    "scripts/weather_daemon/config.py"
    "scripts/weather_daemon/generator.py"
    "scripts/weather_daemon/index_generator.py"
    "scripts/weather_daemon/artcc_boundaries.py"
    "scripts/weather_daemon/service/weather-daemon.service"
    "scripts/weather_daemon/service/weather-daemon.timer"
    "backend/__init__.py"
    "backend/core/__init__.py"
    "backend/core/analysis.py"
    "backend/core/calculations.py"
    "backend/core/groupings.py"
    "backend/core/models.py"
    "backend/core/flights.py"
    "backend/core/controllers.py"
    "backend/data/__init__.py"
    "backend/data/loaders.py"
    "backend/data/weather.py"
    "backend/data/vatsim_api.py"
    "backend/data/atis_filter.py"
    "backend/cache/__init__.py"
    "backend/cache/manager.py"
    "backend/config/__init__.py"
    "backend/config/constants.py"
    "ui/__init__.py"
    "ui/config.py"
    "ui/modals/__init__.py"
    "ui/modals/metar_info.py"
    "airport_disambiguator/__init__.py"
    "airport_disambiguator/disambiguator.py"
    "airport_disambiguator/disambiguation_engine.py"
    "airport_disambiguator/entity_extractor.py"
    "airport_disambiguator/name_processor.py"
    "common.py"
    "data/APT_BASE.csv"
    "data/airports.json"
    "data/iata-icao.csv"
    "data/custom_groupings.json"
    "requirements.txt"
)

echo -e "${YELLOW}Creating directory structure on remote...${NC}"
ssh "$USER@$SERVER_IP" "mkdir -p $REMOTE_PATH/scripts/weather_daemon/service $REMOTE_PATH/backend/core $REMOTE_PATH/backend/data $REMOTE_PATH/backend/cache $REMOTE_PATH/backend/config $REMOTE_PATH/ui/modals $REMOTE_PATH/airport_disambiguator $REMOTE_PATH/data/preset_groupings"

echo -e "${YELLOW}Uploading files...${NC}"
for file in "${FILES[@]}"; do
    local_file="$PROJECT_ROOT/$file"
    if [ -f "$local_file" ]; then
        remote_dir=$(dirname "$REMOTE_PATH/$file")
        echo -e "${CYAN}  $file${NC}"
        scp "$local_file" "$USER@$SERVER_IP:$REMOTE_PATH/$file"
    else
        echo -e "${YELLOW}  Warning: $file not found${NC}"
    fi
done

# Upload preset groupings
echo -e "${YELLOW}Uploading preset groupings...${NC}"
for json_file in "$PROJECT_ROOT/data/preset_groupings/"*.json; do
    if [ -f "$json_file" ]; then
        filename=$(basename "$json_file")
        echo -e "${CYAN}  preset_groupings/$filename${NC}"
        scp "$json_file" "$USER@$SERVER_IP:$REMOTE_PATH/data/preset_groupings/"
    fi
done

echo -e "${YELLOW}Updating systemd services...${NC}"
ssh "$USER@$SERVER_IP" "cp $REMOTE_PATH/scripts/weather_daemon/service/weather-daemon.service /etc/systemd/system/ && cp $REMOTE_PATH/scripts/weather_daemon/service/weather-daemon.timer /etc/systemd/system/ && systemctl daemon-reload"

echo -e "${YELLOW}Installing Python dependencies...${NC}"
ssh "$USER@$SERVER_IP" "cd $REMOTE_PATH && source .venv/bin/activate && pip install -r requirements.txt"

echo -e "${YELLOW}Running weather generation...${NC}"
ssh "$USER@$SERVER_IP" "cd $REMOTE_PATH && sudo -u www-data .venv/bin/python -m scripts.weather_daemon.cli --output /var/www/leftos.dev/weather"

echo ""
echo -e "${GREEN}=== Deployment Complete! ===${NC}"
echo -e "${CYAN}Weather briefings updated at https://leftos.dev/weather/${NC}"
