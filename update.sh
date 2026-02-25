#!/usr/bin/env bash
# update.sh — Pull the latest code and restart services
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USERNAME="$(whoami)"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
info()    { echo -e "${CYAN}  →  $*${RESET}"; }
success() { echo -e "${GREEN}  ✓  $*${RESET}"; }

echo ""
echo -e "${BOLD}AI To-Do App — Updating to latest version${RESET}"
echo "────────────────────────────────────────────"

# Pull latest code
info "Downloading latest updates from GitHub…"
git -C "$SCRIPT_DIR" pull --ff-only
success "Code updated"

# Restart services — kill ALL existing Python processes for this app to prevent
# stale old-code processes from accumulating in the background.
info "Restarting services…"

# 1. Stop via launchctl so it won't immediately respawn
launchctl stop  "com.${USERNAME}.todo-app"       2>/dev/null || true
launchctl stop  "com.${USERNAME}.todo-companion"  2>/dev/null || true
sleep 1

# 2. Force-kill any lingering server.py / scan-companion.py processes
#    (launchctl stop does not always SIGKILL the Python process)
pkill -9 -f "${SCRIPT_DIR}/server.py"       2>/dev/null || true
pkill -9 -f "${SCRIPT_DIR}/scan-companion.py" 2>/dev/null || true
sleep 1

# 3. Start fresh
launchctl start "com.${USERNAME}.todo-app"
launchctl start "com.${USERNAME}.todo-companion"
sleep 2

success "Services restarted"
echo ""
echo -e "${BOLD}Update complete. Your app is running the latest version.${RESET}"
echo ""
