#!/usr/bin/env bash
# Wayland Enricher Automation Script (Option B)
# ==============================================
# Automates the Flowsint UI via Wayland CLI to launch an enricher.
# Prerequisites: wayland CLI at ~/Documents/dev/wayland-mcp/bin/wayland
#                Flowsint UI open in browser at localhost:5173
#
# Usage: bash wayland_run_enricher.sh <email> [enricher_name]
#
# This script uses coordinate-based desktop automation.
# You may need to adjust coordinates for your display.

set -e
WAYLAND="/home/n4s5ti/Documents/dev/wayland-mcp/bin/wayland"
EMAIL="carolinerose1954@aol.com"
ENRICHER="email_to_intelligence"

echo "=== Wayland Enricher Automation ==="
echo "Target: $EMAIL via $ENRICHER"

# Step 1: Take a screenshot to verify we are on the right screen
echo "[1] Taking screenshot..."
"$WAYLAND" shot -o /tmp/flowsint_before.png

# Step 2: Press Super+3 to switch to browser workspace (adjust as needed)
# or use Alt+Tab to cycle
echo "[2] Focusing browser window..."
"$WAYLAND" key press "Alt_L+Tab"
sleep 0.5

# Step 3: Navigate to enricher page (Ctrl+L then type URL + Enter)
echo "[3] Navigating to enricher page..."
"$WAYLAND" key type "http://localhost:5173/dashboard/enrichers"
"$WAYLAND" key press "Return"
sleep 2

# Step 4: Click on the email_to_intelligence enricher
# COORDINATES NEED ADJUSTMENT based on your screen and window position
# Typical: first enricher in grid is around x=400, y=300
echo "[4] Clicking enricher..."
# "$WAYLAND" mouse move 400 300
# "$WAYLAND" mouse click
sleep 1

# Step 5: Enter email address
echo "[5] Entering email..."
# "$WAYLAND" key type "$EMAIL"
# "$WAYLAND" key press "Return"
sleep 2

# Step 6: Click Launch button
echo "[6] Launching enricher..."
# "$WAYLAND" mouse move 500 600
# "$WAYLAND" mouse click
sleep 3

# Step 7: Take screenshot to verify result
echo "[7] Verifying..."
"$WAYLAND" shot -o /tmp/flowsint_after.png

echo "=== Done ==="
echo "Screenshots saved to /tmp/flowsint_before.png and /tmp/flowsint_after.png"
echo "Adjust coordinates by examining the screenshots."
