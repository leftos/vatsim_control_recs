#!/bin/bash
# Server Setup Script for VATSIM Weather Briefing Daemon
# Run this on your DigitalOcean droplet as root
#
# Prerequisites: Ubuntu/Debian server with nginx already installed
# (typically after running leftos.dev/server-setup.sh)

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

INSTALL_DIR="/opt/vatsim-weather-daemon"
VENV_DIR="$INSTALL_DIR/.venv"
OUTPUT_DIR="/var/www/leftos.dev/weather"
REPO_URL="https://github.com/leftos/vatsim_control_recs.git"

echo -e "${GREEN}=== VATSIM Weather Daemon Setup ===${NC}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run as root${NC}"
    exit 1
fi

# Check for nginx
if ! command -v nginx &> /dev/null; then
    echo -e "${RED}Error: nginx not found. Please run server-setup.sh first.${NC}"
    exit 1
fi

echo -e "${YELLOW}Installing system dependencies...${NC}"
apt update
apt install -y python3 python3-pip python3-venv git

echo -e "${YELLOW}Creating output directory...${NC}"
mkdir -p "$OUTPUT_DIR"

# Clone or update repository
if [ -d "$INSTALL_DIR/.git" ]; then
    echo -e "${YELLOW}Updating existing repository...${NC}"
    cd "$INSTALL_DIR"
    git pull
elif [ -d "$INSTALL_DIR" ] && [ "$(ls -A $INSTALL_DIR 2>/dev/null)" ]; then
    # Directory exists but isn't a git repo - back it up and clone fresh
    echo -e "${YELLOW}Directory exists but is not a git repo. Backing up and cloning fresh...${NC}"
    mv "$INSTALL_DIR" "${INSTALL_DIR}.backup.$(date +%s)"
    git clone "$REPO_URL" "$INSTALL_DIR"
else
    echo -e "${YELLOW}Cloning repository...${NC}"
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# Create cache directory after clone
mkdir -p "$INSTALL_DIR/cache"

echo -e "${YELLOW}Creating Python virtual environment...${NC}"
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

echo -e "${YELLOW}Installing Python dependencies...${NC}"
pip install --upgrade pip
pip install -r requirements.txt

echo -e "${YELLOW}Downloading spaCy language model...${NC}"
python -m spacy download en_core_web_sm

echo -e "${YELLOW}Setting up systemd service...${NC}"
cp scripts/weather_daemon/service/weather-daemon.service /etc/systemd/system/
cp scripts/weather_daemon/service/weather-daemon.timer /etc/systemd/system/

# Fix permissions
chown -R www-data:www-data "$OUTPUT_DIR"
chown -R www-data:www-data "$INSTALL_DIR/cache"

# Reload systemd and enable timer
systemctl daemon-reload
systemctl enable weather-daemon.timer

echo -e "${YELLOW}Configuring nginx...${NC}"
# Add weather location to existing nginx config if not present
NGINX_CONF="/etc/nginx/sites-available/leftos.dev"

if [ -f "$NGINX_CONF" ]; then
    if ! grep -q "location /weather" "$NGINX_CONF"; then
        # Insert weather location before the closing brace of the server block
        sed -i '/^}$/i \
    # Weather briefings\
    location /weather/ {\
        alias /var/www/leftos.dev/weather/;\
        index index.html;\
        try_files $uri $uri/ =404;\
\
        # Enable directory listing for ARTCC subdirectories\
        autoindex on;\
        autoindex_exact_size off;\
        autoindex_localtime on;\
\
        # Cache HTML files for 5 minutes\
        location ~* \\.html$ {\
            expires 5m;\
            add_header Cache-Control "public, must-revalidate";\
        }\
    }' "$NGINX_CONF"
        echo -e "${CYAN}Added weather location to nginx config${NC}"
    else
        echo -e "${CYAN}Weather location already configured in nginx${NC}"
    fi
else
    echo -e "${YELLOW}Warning: nginx config not found at $NGINX_CONF${NC}"
    echo -e "${YELLOW}You may need to manually configure nginx${NC}"
fi

# Test nginx config and reload
nginx -t && systemctl reload nginx

echo -e "${YELLOW}Running initial weather generation...${NC}"
sudo -u www-data "$VENV_DIR/bin/python" -m scripts.weather_daemon.cli --output "$OUTPUT_DIR"

echo -e "${YELLOW}Starting timer...${NC}"
systemctl start weather-daemon.timer

echo ""
echo -e "${GREEN}=== Setup Complete! ===${NC}"
echo ""
echo -e "${CYAN}Weather briefings will be generated every 15 minutes.${NC}"
echo -e "${CYAN}View at: https://leftos.dev/weather/${NC}"
echo ""
echo -e "Useful commands:"
echo -e "  ${YELLOW}systemctl status weather-daemon.timer${NC}  # Check timer status"
echo -e "  ${YELLOW}systemctl list-timers${NC}                  # List all timers"
echo -e "  ${YELLOW}journalctl -u weather-daemon${NC}           # View daemon logs"
echo -e "  ${YELLOW}systemctl start weather-daemon${NC}         # Run generation now"
echo ""
