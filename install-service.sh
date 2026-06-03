#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="$SCRIPT_DIR/apollo-ui.service"

if [ ! -f "$SERVICE_FILE" ]; then
  echo "Error: apollo-ui.service not found in $SCRIPT_DIR"
  exit 1
fi

echo "Installing Apollo UI service..."
echo "Make sure you've edited apollo-ui.service with your username and paths first!"
echo ""

sudo cp "$SERVICE_FILE" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable apollo-ui
sudo systemctl start apollo-ui
sudo systemctl status apollo-ui
