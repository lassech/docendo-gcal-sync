#!/bin/bash
# Setup script for Docendo → Google Calendar sync
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Installing Python dependencies..."
pip3 install --break-system-packages -r requirements.txt

echo ""
echo "=== Next step: Google credentials ==="
echo ""
echo "1. Go to https://console.cloud.google.com/"
echo "2. Create a new project (or use existing)"
echo "3. Enable 'Google Calendar API'"
echo "4. Go to 'APIs & Services' → 'Credentials'"
echo "5. Create 'OAuth 2.0 Client ID' → type: 'Desktop application'"
echo "6. Download JSON and save as: $SCRIPT_DIR/credentials.json"
echo ""
echo "Then run: python3 sync.py"
echo "(First run will open a browser to authorize. After that it runs automatically.)"
echo ""

# Add cron job at 06:30 if not already present
CRON_LINE="30 6 * * * cd $SCRIPT_DIR && python3 sync.py >> sync.log 2>&1"
(crontab -l 2>/dev/null | grep -qF "sync.py") && \
    echo "Cron job already exists, skipping." || \
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -

echo "Cron job set: runs daily at 06:30"
crontab -l | grep sync
