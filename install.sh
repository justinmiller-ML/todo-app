#!/usr/bin/env bash
# install.sh — One-command installer for the AI To-Do App
# New users run:  bash <(curl -fsSL https://raw.githubusercontent.com/GITHUB_USER/REPO_NAME/main/install.sh)
set -euo pipefail

REPO_URL="https://github.com/GITHUB_USER/REPO_NAME.git"
INSTALL_DIR="$HOME/todo-app"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
info()    { echo -e "${CYAN}  →  $*${RESET}"; }
success() { echo -e "${GREEN}  ✓  $*${RESET}"; }
error()   { echo -e "${RED}  ✗  $*${RESET}"; exit 1; }

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║           AI To-Do App — Installer                              ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════════╝${RESET}"
echo ""

# Verify placeholder was replaced
if [[ "$REPO_URL" == *"GITHUB_USER"* ]]; then
  error "REPO_URL in install.sh has not been set. Edit install.sh and replace GITHUB_USER/REPO_NAME with the actual GitHub repo path."
fi

# Check for git
if ! command -v git &>/dev/null; then
  error "git is not installed. Install Xcode Command Line Tools: xcode-select --install"
fi

# Clone or update
if [[ -d "$INSTALL_DIR/.git" ]]; then
  info "Existing installation found — pulling latest code…"
  git -C "$INSTALL_DIR" pull --ff-only
  success "Code updated"
else
  if [[ -d "$INSTALL_DIR" ]]; then
    error "$INSTALL_DIR already exists but is not a git repo. Move or delete it first, then re-run."
  fi
  info "Downloading app to $INSTALL_DIR…"
  git clone "$REPO_URL" "$INSTALL_DIR"
  success "Downloaded"
fi

# Hand off to setup
echo ""
info "Running setup…"
bash "$INSTALL_DIR/setup.sh"
