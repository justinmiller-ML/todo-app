#!/usr/bin/env bash
# setup.sh — First-time setup for the AI To-Do App
# Run once on a new machine:  bash setup.sh
set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[0;33m'; GREEN='\033[0;32m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}  →  $*${RESET}"; }
success() { echo -e "${GREEN}  ✓  $*${RESET}"; }
warn()    { echo -e "${YELLOW}  ⚠  $*${RESET}"; }
error()   { echo -e "${RED}  ✗  $*${RESET}"; }
header()  { echo -e "\n${BOLD}$*${RESET}"; echo "────────────────────────────────────────────"; }
ask()     { echo -e "${BOLD}$*${RESET}"; }          # bold prompt label

# ── Resolve script directory ───────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USERNAME="$(whoami)"

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║           AI To-Do App — First-time Setup                       ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════════╝${RESET}"
echo ""
info "Install directory : $SCRIPT_DIR"
info "Running as user   : $USERNAME"
echo ""


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — System prerequisites
# ══════════════════════════════════════════════════════════════════════════════
header "Step 1 — Checking prerequisites"

# macOS check
if [[ "$(uname)" != "Darwin" ]]; then
  error "This app currently requires macOS (uses LaunchAgents for auto-start)."
  exit 1
fi
success "macOS detected"

# Python 3
if ! command -v python3 &>/dev/null; then
  error "Python 3 not found. Install it from https://www.python.org or via Homebrew: brew install python3"
  exit 1
fi
PYTHON_BIN="$(command -v python3)"
success "Python 3 found at $PYTHON_BIN"

# ── Python packages ────────────────────────────────────────────────────────────
info "Installing required Python packages (pdfplumber, python-docx)…"
if "$PYTHON_BIN" -m pip install --quiet --user pdfplumber python-docx 2>&1 | grep -i error; then
  warn "pip install produced errors above — packages may already be installed, continuing."
else
  success "Python packages installed"
fi


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Find Claude CLI binary
# ══════════════════════════════════════════════════════════════════════════════
header "Step 2 — Locating Claude CLI binary"

CLAUDE_PARENT="$HOME/Library/Application Support/Claude/claude-code"
CLAUDE_BIN=""

if [[ -d "$CLAUDE_PARENT" ]]; then
  # Find all 'claude' executables, sort by version (last path component), take latest
  CLAUDE_BIN="$(find "$CLAUDE_PARENT" -maxdepth 3 -name "claude" -type f 2>/dev/null \
    | sort -t'/' -k1,1V | tail -1)"
fi

if [[ -z "$CLAUDE_BIN" ]]; then
  # Fallback: maybe it's on PATH
  CLAUDE_BIN="$(command -v claude 2>/dev/null || true)"
fi

if [[ -z "$CLAUDE_BIN" || ! -x "$CLAUDE_BIN" ]]; then
  error "Claude CLI not found."
  echo ""
  echo "  Please install Claude Code from: https://claude.ai/download"
  echo "  Then re-run this script."
  exit 1
fi
success "Claude CLI found: $CLAUDE_BIN"

# Check Claude login
info "Checking Claude login status…"
if ! "$CLAUDE_BIN" --print --allowedTools '' "Reply with only: ok" &>/dev/null; then
  warn "Claude doesn't appear to be logged in."
  echo ""
  echo "  Please run the following command to log in, then re-run setup.sh:"
  echo "    \"$CLAUDE_BIN\" login"
  echo ""
  read -r -p "  Press Enter after logging in, or Ctrl-C to abort: "
  if ! "$CLAUDE_BIN" --print --allowedTools '' "Reply with only: ok" &>/dev/null; then
    error "Still can't connect to Claude. Please log in first."
    exit 1
  fi
fi
success "Claude is logged in"


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Discover Slack MCP UUID
# ══════════════════════════════════════════════════════════════════════════════
header "Step 3 — Discovering Slack MCP server UUID"

SLACK_UUID=""

# Search all Claude config/settings files for the slack_search tool name
if [[ -d "$HOME/.claude" ]]; then
  SLACK_UUID="$(grep -r "slack_search_public_and_private" "$HOME/.claude" 2>/dev/null \
    | grep -o '[0-9a-f]\{8\}-[0-9a-f]\{4\}-[0-9a-f]\{4\}-[0-9a-f]\{4\}-[0-9a-f]\{12\}' \
    | head -1 || true)"
fi

if [[ -z "$SLACK_UUID" ]]; then
  warn "Could not auto-detect the Slack MCP UUID."
  echo ""
  echo "  This UUID identifies your Slack MCP server inside Claude Code."
  echo "  To find it:"
  echo "   1. Open Claude Code"
  echo "   2. Type: /mcp"
  echo "   3. Look for the Slack entry — the UUID is in the tool name,"
  echo "      e.g.  mcp__bf9c824b-0d5c-418a-a316-210f23e585cc__slack_search..."
  echo ""
  ask "  Paste the UUID here (leave blank to skip Slack scanning): "
  read -r SLACK_UUID
  SLACK_UUID="$(echo "$SLACK_UUID" | tr -d '[:space:]')"
fi

if [[ -n "$SLACK_UUID" ]]; then
  success "Slack MCP UUID: $SLACK_UUID"
else
  warn "Slack UUID not set — Slack scanning will be disabled."
  warn "You can update SLACK_UUID in scan-companion.py later."
fi

SLACK_TOOL="mcp__${SLACK_UUID}__slack_search_public_and_private"


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Collect .env configuration
# ══════════════════════════════════════════════════════════════════════════════
header "Step 4 — Configure credentials (.env)"

# If .env already exists, ask whether to overwrite
ENV_FILE="$SCRIPT_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
  echo ""
  warn ".env already exists at $ENV_FILE"
  ask "  Overwrite with new values? (y/N): "
  read -r overwrite
  if [[ ! "$overwrite" =~ ^[Yy]$ ]]; then
    info "Keeping existing .env — skipping configuration."
    SKIP_ENV=true
  else
    SKIP_ENV=false
  fi
else
  SKIP_ENV=false
fi

if [[ "$SKIP_ENV" == "false" ]]; then
  echo ""
  echo "  You'll need:"
  echo "   • A Gmail App Password (not your regular password)"
  echo "     → myaccount.google.com → Security → 2-Step Verification → App passwords"
  echo "   • (Optional) A Slack Incoming Webhook URL for reminder notifications"
  echo ""

  # USER_NAME
  ask "  Your full name (for task matching): "
  read -r USER_NAME
  USER_NAME="${USER_NAME:-$(id -F 2>/dev/null || echo "Your Name")}"

  # Email
  ask "  Your email address: "
  read -r USER_EMAIL
  USER_EMAIL="${USER_EMAIL:-you@gmail.com}"

  # Derive SMTP_HOST from email domain
  EMAIL_DOMAIN="${USER_EMAIL##*@}"
  case "$EMAIL_DOMAIN" in
    gmail.com)           SMTP_HOST="smtp.gmail.com" ;;
    googlemail.com)      SMTP_HOST="smtp.gmail.com" ;;
    outlook.com|hotmail.com|live.com) SMTP_HOST="smtp.office365.com" ;;
    *)                   SMTP_HOST="smtp.gmail.com" ;;  # fallback, user can edit
  esac

  ask "  SMTP host [$SMTP_HOST]: "
  read -r smtp_host_input
  [[ -n "$smtp_host_input" ]] && SMTP_HOST="$smtp_host_input"

  ask "  Email App Password (input hidden): "
  read -rs SMTP_PASS
  echo ""

  ask "  Reminder email address [same as above — $USER_EMAIL]: "
  read -r REMINDER_EMAIL
  REMINDER_EMAIL="${REMINDER_EMAIL:-$USER_EMAIL}"

  # Slack Webhook (optional)
  ask "  Slack Webhook URL (optional, for reminder notifications — press Enter to skip): "
  read -r SLACK_WEBHOOK
  SLACK_WEBHOOK="${SLACK_WEBHOOK:-}"

  # Slack mention (optional)
  SLACK_MENTION=""
  if [[ -n "$SLACK_WEBHOOK" ]]; then
    ask "  Your Slack user ID for @mentions (e.g. U0A88TGB91T, press Enter to skip): "
    read -r SLACK_MENTION
    [[ -n "$SLACK_MENTION" ]] && SLACK_MENTION="<@${SLACK_MENTION#<@}"; SLACK_MENTION="${SLACK_MENTION%%>*}>"
    SLACK_MENTION="${SLACK_MENTION:-}"
  fi

  # Write .env
  cat > "$ENV_FILE" << EOF
# ── Email (SMTP) ──────────────────────────────────────────────────────────────
# Gmail example: use an App Password (myaccount.google.com → Security → App passwords)
SMTP_HOST=${SMTP_HOST}
SMTP_PORT=587
SMTP_SECURE=false
SMTP_USER=${USER_EMAIL}
SMTP_PASS=${SMTP_PASS}
SMTP_FROM=${USER_EMAIL}

# Where to send reminder emails
REMINDER_EMAIL=${REMINDER_EMAIL}

# ── Slack ─────────────────────────────────────────────────────────────────────
# Create an Incoming Webhook at: https://api.slack.com/messaging/webhooks
SLACK_WEBHOOK=${SLACK_WEBHOOK}
SLACK_MENTION=${SLACK_MENTION}

# ── Slack scanning (optional) ─────────────────────────────────────────────────
# Needs a User OAuth Token (xoxp-...) with search:read scope
# Get from: api.slack.com → Your App → OAuth & Permissions → User Token
SLACK_USER_TOKEN=
SLACK_USER_ID=

# ── Gong scanning (optional) ──────────────────────────────────────────────────
GONG_API_KEY=
GONG_API_SECRET=

# ── Scanner settings ──────────────────────────────────────────────────────────
SCAN_INTERVAL_MINUTES=5
USER_NAME=${USER_NAME}

# ── Server ────────────────────────────────────────────────────────────────────
PORT=3000
EOF
  chmod 600 "$ENV_FILE"
  success ".env written (permissions set to 600 — readable only by you)"
fi


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — Update scan-companion.py with machine-specific paths
# ══════════════════════════════════════════════════════════════════════════════
header "Step 5 — Patching scan-companion.py"

COMPANION="$SCRIPT_DIR/scan-companion.py"
if [[ ! -f "$COMPANION" ]]; then
  error "scan-companion.py not found at $COMPANION"
  exit 1
fi

# Use Python to do safe, in-place substitution (handles spaces in paths)
"$PYTHON_BIN" - "$COMPANION" "$CLAUDE_BIN" "$SLACK_UUID" << 'PYEOF'
import sys, re, pathlib

companion_path = sys.argv[1]
claude_bin     = sys.argv[2]
slack_uuid     = sys.argv[3]

text = pathlib.Path(companion_path).read_text()

# Replace multi-line form:
#   CLAUDE_BIN = os.path.expanduser(
#       '~/...'
#   )
# and single-line forms:
#   CLAUDE_BIN = os.path.expanduser('~/...')
#   CLAUDE_BIN = '...'
text = re.sub(
    r"^CLAUDE_BIN\s*=\s*os\.path\.expanduser\(\s*\n\s*['\"].*?['\"]\s*\n\s*\)",
    f"CLAUDE_BIN = {repr(claude_bin)}",
    text, flags=re.MULTILINE,
)
text = re.sub(
    r"^CLAUDE_BIN\s*=\s*os\.path\.expanduser\(['\"].*?['\"]\)",
    f"CLAUDE_BIN = {repr(claude_bin)}",
    text, flags=re.MULTILINE,
)
text = re.sub(
    r"^CLAUDE_BIN\s*=\s*['\"].*?['\"]",
    f"CLAUDE_BIN = {repr(claude_bin)}",
    text, flags=re.MULTILINE,
)

# Replace SLACK_UUID = '...'
if slack_uuid:
    text = re.sub(
        r"^SLACK_UUID\s*=\s*['\"].*?['\"]",
        f"SLACK_UUID = {repr(slack_uuid)}",
        text, flags=re.MULTILINE,
    )

pathlib.Path(companion_path).write_text(text)
print("ok")
PYEOF

success "scan-companion.py patched with your Claude binary path and Slack UUID"


# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — Create tasks.json if it doesn't exist
# ══════════════════════════════════════════════════════════════════════════════
header "Step 6 — Initialising tasks.json"

TASKS_FILE="$SCRIPT_DIR/tasks.json"
if [[ ! -f "$TASKS_FILE" ]]; then
  echo '{"today":[],"longterm":[]}' > "$TASKS_FILE"
  success "Created empty tasks.json"
else
  success "tasks.json already exists — leaving it as-is"
fi


# ══════════════════════════════════════════════════════════════════════════════
# STEP 7 — Install LaunchAgents
# ══════════════════════════════════════════════════════════════════════════════
header "Step 7 — Installing macOS LaunchAgents (auto-start on login)"

LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$LAUNCH_AGENTS_DIR"

# ── Server LaunchAgent ────────────────────────────────────────────────────────
SERVER_PLIST="$LAUNCH_AGENTS_DIR/com.${USERNAME}.todo-app.plist"
cat > "$SERVER_PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.${USERNAME}.todo-app</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_BIN}</string>
        <string>${SCRIPT_DIR}/server.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>${SCRIPT_DIR}/server.log</string>
    <key>StandardErrorPath</key>
    <string>${SCRIPT_DIR}/server.log</string>
</dict>
</plist>
EOF
success "Server LaunchAgent written: $SERVER_PLIST"

# ── Companion LaunchAgent ─────────────────────────────────────────────────────
COMPANION_PLIST="$LAUNCH_AGENTS_DIR/com.${USERNAME}.todo-companion.plist"
cat > "$COMPANION_PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.${USERNAME}.todo-companion</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_BIN}</string>
        <string>${SCRIPT_DIR}/scan-companion.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>

    <!-- HOME is required so Claude CLI can find auth tokens in ~/.claude/ -->
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>${HOME}</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>${SCRIPT_DIR}/companion.log</string>
    <key>StandardErrorPath</key>
    <string>${SCRIPT_DIR}/companion.log</string>
</dict>
</plist>
EOF
success "Companion LaunchAgent written: $COMPANION_PLIST"

# ── Load / reload both agents ─────────────────────────────────────────────────
info "Loading LaunchAgents…"

# Stop any already-running instances gracefully
for label in "com.${USERNAME}.todo-app" "com.${USERNAME}.todo-companion" \
             "com.user.todo-app"        "com.user.todo-companion"; do
  launchctl stop "$label" 2>/dev/null || true
  launchctl unload "$LAUNCH_AGENTS_DIR/${label}.plist" 2>/dev/null || true
done

# Also kill any stray Python processes from this directory
pkill -f "python3.*${SCRIPT_DIR}/server.py"     2>/dev/null || true
pkill -f "python3.*${SCRIPT_DIR}/scan-companion" 2>/dev/null || true
sleep 1

launchctl load "$SERVER_PLIST"
launchctl load "$COMPANION_PLIST"

sleep 2   # Give the server a moment to start
success "LaunchAgents loaded — both services will start automatically on login"


# ══════════════════════════════════════════════════════════════════════════════
# STEP 8 — Verify server started
# ══════════════════════════════════════════════════════════════════════════════
header "Step 8 — Verifying server"

PORT=3000
if grep -q "^PORT=" "$ENV_FILE" 2>/dev/null; then
  PORT="$(grep "^PORT=" "$ENV_FILE" | cut -d= -f2)"
fi

MAX_WAIT=12
i=0
until curl -sf "http://localhost:${PORT}/api/tasks" >/dev/null 2>&1; do
  sleep 1
  i=$((i+1))
  if [[ $i -ge $MAX_WAIT ]]; then
    warn "Server did not respond within ${MAX_WAIT}s."
    warn "Check the log: tail -f $SCRIPT_DIR/server.log"
    break
  fi
done

if curl -sf "http://localhost:${PORT}/api/tasks" >/dev/null 2>&1; then
  success "Server is running at http://localhost:${PORT}"
fi


# ══════════════════════════════════════════════════════════════════════════════
# Done!
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║   Setup complete!                                                ║${RESET}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  ${BOLD}App URL${RESET}      : http://localhost:${PORT}"
echo -e "  ${BOLD}Server log${RESET}   : $SCRIPT_DIR/server.log"
echo -e "  ${BOLD}Companion log${RESET}: $SCRIPT_DIR/companion.log"
echo -e "  ${BOLD}Config${RESET}       : $SCRIPT_DIR/.env"
echo ""
echo "  Both services start automatically at login."
echo "  To view logs at any time:"
echo "    tail -f $SCRIPT_DIR/server.log"
echo "    tail -f $SCRIPT_DIR/companion.log"
echo ""

# Open browser
open "http://localhost:${PORT}" 2>/dev/null || true
