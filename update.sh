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

# Restart services
info "Restarting services…"
launchctl stop  "com.${USERNAME}.todo-app"       2>/dev/null || true
launchctl stop  "com.${USERNAME}.todo-companion"  2>/dev/null || true
sleep 1
launchctl start "com.${USERNAME}.todo-app"
launchctl start "com.${USERNAME}.todo-companion"
sleep 2

success "Services restarted"
echo ""
echo -e "${BOLD}Update complete. Your app is running the latest version.${RESET}"
echo ""
