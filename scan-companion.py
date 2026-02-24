#!/usr/bin/env python3
"""
scan-companion.py — Bridges the Scan Now button + Notes ingest with Claude Code's MCP tools.

WHY THIS EXISTS
───────────────
The Slack/Gong MCP servers inside Claude Code are "SDK-type" — they live inside
the Claude desktop app and can't be reached by server.py directly (no API keys
needed or wanted).  This script runs as a sidecar process in a regular terminal
(outside Claude Code) and does two things:

  1. SCAN NOW trigger   — watches .scan-trigger and searches Slack for new action items
  2. Notes ingest queue — watches .ingest-queue/ and extracts action items from
                          meeting notes, call transcripts, and Gemini notes emails

USAGE
─────
  1. Open a new Terminal window (NOT inside Claude Code — that would set CLAUDECODE
     and block the nested session).
  2. Run:  python3 ~/todo-app/scan-companion.py
  3. Click "Scan Now" or "Add Notes → Extract" in the browser.
  4. Leave it running; it re-triggers on every click.
  5. Ctrl-C to stop.

TROUBLESHOOTING
───────────────
• Timeout on Slack scan    → MCP SDK tools may not be accessible from standalone CLI.
      Workaround: the notes ingest still works (uses only Read/Write tools), so you
      can paste transcripts via the browser UI and they'll be processed correctly.
      For Slack, paste .scan-prompt.txt into a Claude Code chat window.
• "Binary not found"    → Update CLAUDE_BIN below.
      Find it: ls ~/Library/Application\ Support/Claude/claude-code/
• "Cannot be launched inside another Claude Code session"
      → Run this script from a plain Terminal tab, not from Claude Code's terminal.
"""

import subprocess
import os
import time
import json
import datetime
import pathlib
import random
import string

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR         = pathlib.Path(__file__).parent
TRIGGER_FILE     = BASE_DIR / '.scan-trigger'
INGEST_QUEUE_DIR = BASE_DIR / '.ingest-queue'
ALIVE_FILE       = BASE_DIR / '.scan-companion-alive'
PROMPT_FILE      = BASE_DIR / '.scan-prompt.txt'   # fallback: paste into Claude Code
TASKS_FILE       = BASE_DIR / 'tasks.json'

CLAUDE_BIN = os.path.expanduser(
    '~/Library/Application Support/Claude/claude-code/2.1.49/claude'
)

# MCP server UUID — matches the Slack MCP server configured in Claude Code.
# If tool names ever change (e.g. after a Claude Code update), update this UUID.
SLACK_UUID = 'bf9c824b-0d5c-418a-a316-210f23e585cc'
SLACK_TOOL = f'mcp__{SLACK_UUID}__slack_search_public_and_private'

# ── Prompts ───────────────────────────────────────────────────────────────────
def _build_slack_prompt():
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    today_str  = datetime.date.today().isoformat()
    return f"""\
You are an action-item scanner for Justin Miller (justin.miller@pactum.com, @U0A88TGB91T).

STEP 1 — Read current tasks:
  Read the file {TASKS_FILE} to collect all existing task texts (to avoid duplicates).

STEP 2 — Search Slack for recent messages:
  • slack_search_public_and_private  query="<@U0A88TGB91T> after:{yesterday}"  limit=20
  • slack_search_public_and_private  query="Justin Miller after:{yesterday}"   limit=10

STEP 3 — Identify genuine action items assigned to Justin from the last 24 hours.
  INCLUDE: explicit asks, assignments, follow-up requests, decisions needed.
  EXCLUDE: calendar notifications, FYI-only messages, messages Justin sent himself,
           casual mentions, meeting acceptances, auto-generated notifications.

STEP 4 — For each new action item NOT already in tasks.json:
  Build a task object with this exact schema:
    id          : 8 random alphanumeric chars + hex(current unix milliseconds)
                  e.g. "ab3x9kqz19c88d3f477"
    text        : clean, specific, verb-first title (≤120 chars)
                  good: "Reply to Mike about expanding Campaigns training"
                  bad:  "Mike said Justin should reply"
    priority    : "high" if urgent/ASAP/today mentioned, else "medium"
    due         : "YYYY-MM-DD" if a deadline is mentioned, else null
    done        : false
    created     : current unix milliseconds (integer)
    auto        : true
    source      : "slack"
    sourceDetail: "#channel-name — Sender Name (Feb DD)"

STEP 5 — Write the updated tasks.json using the Write tool.
  • Place tasks with no due date, or due on or before {today_str}, in "today" array.
  • Place tasks with a future due date in "longterm" array.
  • PRESERVE every existing task exactly — only append new ones.
  • Never create duplicates (fuzzy-match on text).

Reply with ONLY a compact JSON summary (no other text):
{{"added": N, "skipped_duplicates": K, "tasks_added": ["task text 1", ...]}}
"""


def _build_notes_prompt(text, source):
    today_str = datetime.date.today().isoformat()
    # Truncate very long transcripts to fit in context (keep first 12k chars)
    text_trunc = text[:12000] + ('\n\n[... truncated ...]' if len(text) > 12000 else '')
    return f"""\
You are an action-item extractor for Justin Miller (justin.miller@pactum.com, @U0A88TGB91T).
You have been given the following meeting notes or call transcript.

SOURCE: {source}
─────────────────────────────────────────────────────────────────
{text_trunc}
─────────────────────────────────────────────────────────────────

STEP 1 — Read current tasks:
  Read the file {TASKS_FILE} to collect all existing task texts (to avoid duplicates).

STEP 2 — From the content above, identify ALL action items that Justin Miller is
  responsible for completing. Be thorough — include anything that:
  • Was explicitly assigned to Justin or he agreed to do
  • He volunteered for during the meeting
  • Is a clear next step tied to his role
  EXCLUDE: items assigned to other people, general decisions, FYI notes.

STEP 3 — For each new action item NOT already in tasks.json:
  Build a task object with this exact schema:
    id          : 8 random alphanumeric chars + hex(current unix milliseconds)
                  e.g. "ab3x9kqz19c88d3f477"
    text        : clean, specific, verb-first title (≤120 chars)
                  good: "Follow up with Drew on Cleveland Clinic starter pack scope"
                  bad:  "Justin will follow up"
    priority    : "high" if urgent/ASAP/today, else "medium"
    due         : "YYYY-MM-DD" if a deadline is mentioned, else null
    done        : false
    created     : current unix milliseconds (integer)
    auto        : true
    source      : "notes"
    sourceDetail: "{source[:80]}"

STEP 4 — Write the updated tasks.json using the Write tool.
  • Place tasks with no due date, or due on or before {today_str}, in "today" array.
  • Place tasks with a future due date in "longterm" array.
  • PRESERVE every existing task exactly — only append new ones.
  • Never create duplicates (fuzzy-match on text).

Reply with ONLY a compact JSON summary (no other text):
{{"added": N, "skipped_duplicates": K, "tasks_added": ["task text 1", ...]}}
"""


# ── Helpers ───────────────────────────────────────────────────────────────────
def _clean_env():
    """Strip CLAUDECODE so the subprocess isn't blocked as a nested session."""
    env = {k: v for k, v in os.environ.items() if k != 'CLAUDECODE'}
    env['HOME'] = os.path.expanduser('~')
    return env


def _run_claude(prompt, tools, label, timeout=180):
    """Run claude --print with the given prompt and tool list. Returns (ok, output)."""
    # --allowedTools accepts a comma-separated list; pass all tools in one flag
    args = [CLAUDE_BIN, '--print', '--allowedTools', ','.join(tools)]

    env = _clean_env()
    try:
        r = subprocess.run(
            args,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd='/tmp',   # neutral dir — no project context loaded
        )
        out = r.stdout.strip()
        err = r.stderr.strip()

        if r.returncode == 0:
            try:
                summary = json.loads(out)
                added   = summary.get('added', '?')
                skipped = summary.get('skipped_duplicates', '?')
                tasks   = summary.get('tasks_added', [])
                print(f'[{datetime.datetime.now():%H:%M:%S}] ✅  {label} — '
                      f'{added} added, {skipped} duplicates skipped')
                for t in tasks:
                    print(f'         + {t[:100]}')
            except json.JSONDecodeError:
                print(f'[{datetime.datetime.now():%H:%M:%S}] ✅  {label} (non-JSON):')
                print(f'         {out[:300]}')
            return True, out
        else:
            print(f'[{datetime.datetime.now():%H:%M:%S}] ❌  {label} — exit {r.returncode}')
            # Print both stdout and stderr for easier debugging
            if out:
                print(f'    stdout: {out[:600]}')
            if err:
                print(f'    stderr: {err[:400]}')
            return False, err

    except subprocess.TimeoutExpired:
        print(f'[{datetime.datetime.now():%H:%M:%S}] ❌  {label} — timed out after {timeout}s')
        return False, 'timeout'
    except FileNotFoundError:
        print(f'[{datetime.datetime.now():%H:%M:%S}] ❌  Claude binary not found:')
        print(f'    {CLAUDE_BIN}')
        print(f'    Update CLAUDE_BIN in scan-companion.py')
        return False, 'binary not found'
    except Exception as e:
        print(f'[{datetime.datetime.now():%H:%M:%S}] ❌  {label} — {e}')
        return False, str(e)


def _touch_alive():
    """Write a heartbeat so server.py knows the companion is running."""
    try:
        ALIVE_FILE.write_text(datetime.datetime.now().isoformat())
    except Exception:
        pass


def _remove_alive():
    try:
        ALIVE_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ── Slack scan (triggered by Scan Now button) ─────────────────────────────────
def run_slack_scan():
    prompt = _build_slack_prompt()
    try:
        PROMPT_FILE.write_text(prompt)   # save fallback for manual pasting
    except Exception:
        pass

    print(f'[{datetime.datetime.now():%H:%M:%S}] ▶  Slack scan (timeout 3 min)…')
    ok, _ = _run_claude(
        prompt,
        tools=['Read', 'Write', SLACK_TOOL],
        label='Slack scan',
        timeout=180,
    )
    if not ok:
        print(f'    ⚠  Fallback: paste {PROMPT_FILE} into Claude Code to scan manually.')


# ── Notes ingest queue ────────────────────────────────────────────────────────
def process_ingest_queue():
    """Process all files in the ingest queue directory."""
    INGEST_QUEUE_DIR.mkdir(exist_ok=True)
    items = sorted(INGEST_QUEUE_DIR.glob('*.json'))
    if not items:
        return

    print(f'[{datetime.datetime.now():%H:%M:%S}] 📋  {len(items)} notes item(s) in queue…')
    for item_path in items:
        try:
            data   = json.loads(item_path.read_text())
            text   = data.get('text', '').strip()
            source = data.get('source', 'Unknown source')
            if not text:
                item_path.unlink(missing_ok=True)
                continue

            short = source[:55]
            print(f'[{datetime.datetime.now():%H:%M:%S}] ▶  Extracting from: {short}…')
            prompt = _build_notes_prompt(text, source)

            ok, _ = _run_claude(
                prompt,
                tools=['Read', 'Write'],   # notes extraction only needs file tools
                label=f'Notes: {short}',
                timeout=120,
            )
            if ok:
                item_path.unlink(missing_ok=True)
            else:
                # Rename to .err so it can be inspected or manually retried
                err_path = item_path.with_suffix('.err')
                item_path.rename(err_path)
                print(f'    ⚠  Kept as {err_path.name} — rename to .json to retry.')

        except Exception as e:
            print(f'[{datetime.datetime.now():%H:%M:%S}] ❌  Error processing {item_path.name}: {e}')
            try:
                item_path.rename(item_path.with_suffix('.err'))
            except Exception:
                pass


# ── Quick connectivity test ───────────────────────────────────────────────────
def _quick_test():
    """Print a one-liner to verify Slack MCP is reachable from a fresh terminal."""
    test_cmd = (
        f"'{CLAUDE_BIN}' --print --no-session-persistence "
        f"--allowedTools '{SLACK_TOOL}' "
        f"<<< 'Use slack_search_public_and_private with query=\"test\" limit=1. "
        f"Reply ONLY: {{\"ok\": true}}'"
    )
    print('─' * 70)
    print('  Slack MCP test (run in a SEPARATE terminal tab to verify access):')
    print(f'  {test_cmd}')
    print('  Expected: {"ok": true}   |   Timeout = Slack MCP not reachable from CLI')
    print('  Notes ingest (Read/Write tools) works regardless of this test result.')
    print('─' * 70)


# ── Main loop ─────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print()
    print('╔════════════════════════════════════════════════════════════════════╗')
    print('║  scan-companion.py  —  Scan Now + Notes ingest bridge             ║')
    print('╚════════════════════════════════════════════════════════════════════╝')
    print(f'  Tasks file   : {TASKS_FILE}')
    print(f'  Scan trigger : {TRIGGER_FILE}')
    print(f'  Notes queue  : {INGEST_QUEUE_DIR}/')
    print(f'  Claude       : {CLAUDE_BIN}')
    print()

    if not pathlib.Path(CLAUDE_BIN).exists():
        print(f'⚠  WARNING: Claude binary not found at the path above.')
        print(f'   Update CLAUDE_BIN in this script to match your installation.')
        print(f'   Find it: ls ~/Library/Application\\ Support/Claude/claude-code/')
        print()

    _quick_test()
    print()
    print('  Watching for:')
    print('    • Scan Now trigger      → searches Slack for new action items')
    print('    • Notes in ingest queue → extracts action items from meeting notes')
    print('  Ctrl-C to stop.')
    print()

    INGEST_QUEUE_DIR.mkdir(exist_ok=True)
    _touch_alive()

    try:
        while True:
            _touch_alive()

            # Check for Scan Now trigger
            if TRIGGER_FILE.exists():
                try:
                    TRIGGER_FILE.unlink()
                except Exception:
                    pass
                run_slack_scan()
                print()

            # Check for queued notes
            process_ingest_queue()

            time.sleep(3)

    except KeyboardInterrupt:
        print('\nStopped.')

    finally:
        _remove_alive()
