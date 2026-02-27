#!/usr/bin/env python3
"""
Todo app server — pure Python stdlib, no pip required.
Serves the frontend and handles task storage, email, Slack, Gong, and reminders.
"""

import json
import os
import pathlib
import smtplib
import ssl
import threading
import urllib.request
import urllib.parse
import urllib.error
import datetime
import imaplib
import email as email_lib
from email.header import decode_header as email_decode_header
import base64
import random
import string
import re
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from email.mime.text import MIMEText

# ── Optional document-extraction libs ─────────────────────────────────────────
try:
    import pdfplumber as _pdfplumber
    _PDF_OK = True
except ImportError:
    _PDF_OK = False

try:
    import docx as _docx_module
    _DOCX_OK = True
except ImportError:
    _DOCX_OK = False

# ── Config ────────────────────────────────────────────────────────────────────
BASE_DIR       = pathlib.Path(__file__).parent
DATA_FILE      = BASE_DIR / 'tasks.json'
PROCESSED_FILE = BASE_DIR / 'processed.json'
ENV_FILE       = BASE_DIR / '.env'
PUBLIC         = BASE_DIR / 'public'
LOG_FILE       = BASE_DIR / 'server.log'
SCAN_TRIGGER    = BASE_DIR / '.scan-trigger'    # written by /api/scan, read by scan-companion.py
INGEST_QUEUE_DIR = BASE_DIR / '.ingest-queue'  # meeting notes queued here for AI extraction

# ── Logging ───────────────────────────────────────────────────────────────────
_log_lock = threading.Lock()

def log(msg):
    ts   = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}\n'
    with _log_lock:
        with open(LOG_FILE, 'a') as f:
            f.write(line)

# ── Environment ───────────────────────────────────────────────────────────────
def load_env():
    cfg = {}
    if not ENV_FILE.exists():
        return cfg
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, _, v = line.partition('=')
        cfg[k.strip()] = v.strip()
    return cfg

ENV = load_env()

def env(key, default=''):
    return ENV.get(key, os.environ.get(key, default))

PORT          = int(env('PORT', '3000'))
SLACK_MENTION = env('SLACK_MENTION', '')
USER_NAME     = env('USER_NAME', 'Justin Miller')
USER_FIRST    = USER_NAME.split()[0]

# ── Helpers ───────────────────────────────────────────────────────────────────
def uid():
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choices(chars, k=8)) + hex(int(datetime.datetime.now().timestamp() * 1000))[2:]

# ── Task storage ──────────────────────────────────────────────────────────────
_task_lock = threading.Lock()

def load_tasks():
    with _task_lock:
        if not DATA_FILE.exists():
            return {'today': [], 'longterm': []}
        try:
            content = DATA_FILE.read_text()
            if not content.strip():
                raise ValueError('empty file')
            return json.loads(content)
        except Exception as e:
            log(f'[load_tasks] JSON parse error — tasks.json is corrupt: {e}')
            backup = DATA_FILE.with_suffix('.json.corrupt')
            # ── Recover from existing good backup before overwriting it ────────
            if backup.exists():
                try:
                    data = json.loads(backup.read_text())
                    if isinstance(data.get('today'), list):
                        log('[load_tasks] Recovered from .json.corrupt — restoring main file')
                        tmp = DATA_FILE.with_suffix('.json.tmp')
                        tmp.write_text(json.dumps(data, indent=2))
                        tmp.replace(DATA_FILE)
                        return data
                except Exception:
                    pass
            # ── No valid backup; save corrupt file for inspection ──────────────
            try:
                DATA_FILE.rename(backup)
                log(f'[load_tasks] Corrupt file saved to {backup.name}')
            except Exception:
                pass
            return {'today': [], 'longterm': []}

def save_tasks(data):
    with _task_lock:
        # Write to a temp file first, then atomically rename — prevents corrupt
        # tasks.json if the process is killed mid-write or two writers race.
        tmp = DATA_FILE.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(DATA_FILE)   # atomic on POSIX (macOS/Linux)

# ── Processed-message tracking ────────────────────────────────────────────────
_proc_lock  = threading.Lock()

def _load_processed():
    if not PROCESSED_FILE.exists():
        return set()
    try:
        return set(json.loads(PROCESSED_FILE.read_text()))
    except Exception:
        return set()

_processed = _load_processed()

def is_processed(pid):
    return pid in _processed

def mark_processed(pid):
    with _proc_lock:
        _processed.add(pid)
        items = list(_processed)[-10000:]   # cap to avoid unbounded growth
        PROCESSED_FILE.write_text(json.dumps(items))

# ── Rule-based action item extraction (no API key required) ───────────────────
def extract_action_items(source_type, content):
    """
    Detect action items assigned to USER_NAME / USER_FIRST using keyword patterns.
    Returns a list of dicts: {text, due_date, priority}
    """
    today = datetime.date.today()

    # ── Name patterns ────────────────────────────────────────────────────────
    _name_re = re.compile(
        r'(?:^|[\s,;:([@])(?:' + re.escape(USER_NAME) + r'|' + re.escape(USER_FIRST) + r')(?:\b|$)',
        re.IGNORECASE,
    )

    # ── Action-trigger phrases ────────────────────────────────────────────────
    _trigger_re = re.compile(
        r'\b(?:can you|could you|please|would you mind|i need you to|you(?:\s+will)?\s+need to|'
        r'you\s+should|you\s+must|action\s+item|action:|todo:|to-?do:|follow[- ]?up|'
        r'next\s+step|assigned\s+to|your\s+task|your\s+action|remind\s+you|'
        r"don'?t\s+forget|make\s+sure\s+you|ensure\s+you|you\s+(?:are|were)\s+asked)\b",
        re.IGNORECASE,
    )

    # ── Date helpers ──────────────────────────────────────────────────────────
    _MONTHS = {
        'january': 1, 'february': 2, 'march': 3,     'april': 4,
        'may': 5,     'june': 6,     'july': 7,       'august': 8,
        'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4,
        'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
    }
    _WEEKDAYS = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
        'friday': 4, 'saturday': 5, 'sunday': 6,
    }

    def _next_weekday(name):
        target = _WEEKDAYS[name.lower()]
        diff   = (target - today.weekday() + 7) % 7 or 7
        return today + datetime.timedelta(days=diff)

    def _parse_date(text):
        t = text.lower()
        if re.search(r'\b(eod|cob|end\s+of\s+(the\s+)?day)\b', t):
            return today.isoformat()
        if re.search(r'\bend\s+of\s+(the\s+)?week\b', t):
            diff = (4 - today.weekday()) % 7 or 7
            return (today + datetime.timedelta(days=diff)).isoformat()
        if re.search(r'\bend\s+of\s+(the\s+)?month\b', t):
            nm = today.replace(day=28) + datetime.timedelta(days=4)
            return (nm - datetime.timedelta(days=nm.day)).isoformat()
        # "by/on/due [next] weekday"
        m = re.search(
            r'\b(?:by|on|due)\s+(?:next\s+)?(' + '|'.join(_WEEKDAYS) + r')\b', t)
        if m:
            return _next_weekday(m.group(1)).isoformat()
        # "March 5th 2025" / "5th March"
        m = re.search(
            r'\b(' + '|'.join(_MONTHS) + r')\s+(\d{1,2})(?:st|nd|rd|th)?\s*(?:,?\s*(\d{4}))?\b', t)
        if m:
            try:
                month, day = _MONTHS[m.group(1)], int(m.group(2))
                year       = int(m.group(3)) if m.group(3) else today.year
                d = datetime.date(year, month, day)
                if d < today:
                    d = d.replace(year=today.year + 1)
                return d.isoformat()
            except ValueError:
                pass
        # YYYY-MM-DD
        m = re.search(r'\b(\d{4})-(\d{2})-(\d{2})\b', t)
        if m:
            try:
                return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
            except ValueError:
                pass
        # MM/DD or MM/DD/YYYY
        m = re.search(r'\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b', t)
        if m:
            try:
                yr = int(m.group(3)) if m.group(3) else today.year
                if yr < 100:
                    yr += 2000
                return datetime.date(yr, int(m.group(1)), int(m.group(2))).isoformat()
            except ValueError:
                pass
        return None

    _due_phrase_re = re.compile(
        r'\b(?:by|due|before|no\s+later\s+than)\s+([\w/\-,\s]{2,35})', re.IGNORECASE)

    def _extract_due(sentence):
        m = _due_phrase_re.search(sentence)
        return _parse_date(m.group(0)) if m else None

    def _priority(sentence):
        s = sentence.lower()
        if re.search(r'\b(urgent|asap|immediately|critical|blocker|p0|p1|top\s+priority)\b', s):
            return 'high'
        if re.search(r'\b(important|high\s+priority|soon|today|eod|cob|end\s+of\s+day)\b', s):
            return 'high'
        return 'medium'

    def _clean(s):
        s = s.strip()
        s = re.sub(r'^[-•*>\u2022\u25cf]+\s*', '', s)   # strip bullet chars
        s = re.sub(r'\s{2,}', ' ', s)
        return s[:1].upper() + s[1:] if s else s

    # ── Split content into candidate lines ───────────────────────────────────
    lines = [l.strip() for l in re.split(r'[\n\r]+|(?<=[.!?])\s+', content) if l.strip()]

    items      = []
    seen_keys  = set()

    for i, line in enumerate(lines):
        # ── Hard-skip patterns — obvious non-action-items ─────────────────────
        # Inline image references from email clients
        if re.match(r'^\[image:', line, re.IGNORECASE):
            continue
        # Email salutation lines: "Hi Justin,", "G'day Justin,", "Hello team,"
        if re.match(r'^(?:hi|hello|hey|dear|g\'?day|good\s+(?:morning|afternoon|evening|day))\b',
                    line, re.IGNORECASE) and len(line.split()) <= 20:
            continue
        # Document byline / attribution lines: "By Justin Miller", "By Justin"
        if re.match(r'^by\s+[A-Z][a-z]+(\s+[A-Z][a-z]+)*\s*$', line):
            continue
        # Ownership / role label lines: "Owner: Justin, Drew"
        if re.match(r'^(?:owner|lead|responsible|assignee|poc|dri|point\s+of\s+contact)\s*:',
                    line, re.IGNORECASE):
            continue
        # Email thread reply headers: "On Tue, Feb 24 at 3:13 AM Someone <email> wrote:"
        # Also catches wrapped headers ending with the bare email address e.g. <name@domain>
        if (re.match(r'^on\s+\w', line, re.IGNORECASE) and
                re.search(r'wrote:\s*$|<\s*$|@[\w.\-]+>\s*$', line, re.IGNORECASE)):
            continue
        # Quoted reply attribution: "someone@domain.com> wrote:"
        if re.search(r'@[\w.\-]+>\s*wrote:\s*$', line, re.IGNORECASE):
            continue
        # Corporate footer / legal boilerplate lines
        if re.match(r'^(?:google\s+llc|you\s+have\s+received\s+this\s+email\s+because'
                    r'|this\s+email\s+was\s+sent\s+to|unsubscribe|privacy\s+policy'
                    r'|©\s*\d{4}|all\s+rights\s+reserved'
                    r'|\d+\s+\w+.*(?:ave|blvd|pkwy|way|street|road|drive).*\busa\b'
                    r'|mountain\s+view|1600\s+amphitheatre)', line, re.IGNORECASE):
            continue
        # "Name shared a document/file" — Google Drive share notifications in reply bodies
        if re.match(r'^[\w\s.\-]+ shared (a |an )?(document|file|folder|spreadsheet|slide)',
                    line, re.IGNORECASE):
            continue
        # "Name (email) has invited you to..." — Google Docs invite lines (no word-count limit)
        if re.match(r'^[\w\s.\-]+\([\w.\-\+]+@[\w.\-]+\)\s+has\s+(invited|shared)',
                    line, re.IGNORECASE):
            continue
        # Raw email address lines, or "Name <email>" / "Name (email)" standing alone
        if re.match(r'^[\w\s.\-]+([\(<])[\w.\-\+]+@[\w.\-]+\.(com|net|org|io)[>\)]\s*'
                    r'(?:has\s+invited|shared|wrote|said)?',
                    line, re.IGNORECASE) and len(line.split()) <= 15:
            continue
        # Standalone email address lines or "email> wrote:" fragments
        if re.match(r'^[\w.\-\+]+@[\w.\-]+\s*', line) and len(line.split()) <= 4:
            continue
        # Email header lines appearing inside body (quoted / forwarded email blocks)
        # Allow optional leading ">" chars — quoted blocks use "> From:", "> Date:", etc.
        if re.match(r'^[>\s]*(?:from|to|cc|bcc|subject|date|reply-to|message-id|'
                    r'delivered-to|received|x-[\w-]+)\s*:',
                    line, re.IGNORECASE):
            continue
        # Calendar event field labels: "Organizer:", "When:", "Where:", "Attendees:", etc.
        if re.match(r'^[>\s]*(?:organizer|when|where|attendees?|time|location|'
                    r'event(?:\s+title)?|join\s+(?:zoom|the\s+meeting)|'
                    r'dial[\s\-]?in|conference\s+(?:id|room)|'
                    r'proposed\s+(?:new\s+)?time|video\s+call|meeting\s+link)\s*[:\-]',
                    line, re.IGNORECASE):
            continue
        # Standalone domain / URL lines: "Pactum.com", "www.pactum.ai"
        if re.match(r'^(?:www\.)?[\w\-]+\.(com|net|org|io|ai|co|us)\s*$', line, re.IGNORECASE):
            continue
        # Email signature contact info: phone number paired with email address
        if (re.search(r'\+?\d[\d\s.\-\(\)]{6,}\d', line) and
                re.search(r'@[\w.\-]+', line)):
            continue
        # Time-slot-only lines: "8:30am (CDT)", "10:30am - 11am (CST) (Justin Miller)"
        if re.match(r'^\d{1,2}:\d{2}\s*(?:am|pm)\b', line, re.IGNORECASE):
            continue
        # Day-of-week date/time lines: "Tue Apr 28, 2026 9am – 9:30am"
        if (re.match(r'^(?:mon|tue|wed|thu|fri|sat|sun)\w*\b', line, re.IGNORECASE) and
                re.search(r'\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b', line, re.IGNORECASE) and
                len(line.split()) <= 12):
            continue
        # Standalone person-name lines: "Justin Miller", "Justin Miller - organizer"
        # A bare name with no action verb is never an action item.
        if (re.match(r'^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\s*(?:[-–]\s*[\w\s]+)?$', line) and
                len(line.split()) <= 6 and
                not re.search(r'\b(?:please|must|should|need|action|follow|next\s+step)\b',
                              line, re.IGNORECASE)):
            continue
        # Lines ending with ":" are section headers / list introducers, not action items
        # e.g. "The next step is confirming your preferred setup approach:"
        if line.rstrip().endswith(':'):
            continue
        # Line-wrapped sentence fragments — end with a word that almost never closes
        # a complete sentence: prepositions, conjunctions, articles, determiners, pronouns.
        # e.g. "…who from Pactum would be best to"  (ends with preposition)
        # e.g. "If at any point you have questions please let me"  (ends with pronoun)
        # e.g. "Please review the"  (ends with article)
        if re.search(
            r'\b(?:to|and|or|but|for|in|of|at|by|from|with|into|onto|over|through|about'
            r'|the|a|an|this|that|these|those|your|our|their|its|my|his|her'
            r'|me|us|them|him|her)\s*$',
            line, re.IGNORECASE,
        ) and len(line.split()) >= 5:
            continue
        # Lines containing 2+ email addresses — To:/CC: continuation lines from forwarded mail
        # e.g. "Lehmanc@ccf.org>, <hutchij@ccf.org>, Sara Bunjaku <bunjaks@ccf.org>"
        if len(re.findall(r'[\w.\-\+]+@[\w.\-]+', line)) >= 2:
            continue

        # include next line for context (e.g. "Justin:" then action on next line)
        ctx = line + (' ' + lines[i + 1] if i + 1 < len(lines) else '')

        has_name    = bool(_name_re.search(ctx))
        has_trigger = bool(_trigger_re.search(ctx))

        if not (has_name or has_trigger):
            continue

        # ── Name-only match refinements ───────────────────────────────────────
        if has_name and not has_trigger:
            # Skip if name appears ONLY inside parentheses (role label, not directive)
            ctx_no_parens = re.sub(r'\([^)]*\)', ' ', ctx)
            if not _name_re.search(ctx_no_parens):
                continue
            # Skip third-person descriptions: "[Name] will/is/was/has/can/should..."
            if re.match(
                r'^(?:' + re.escape(USER_NAME) + r'|' + re.escape(USER_FIRST) + r')\s+'
                r'(?:will|is|was|has|had|can|could|would|should|may|might)\b',
                line, re.IGNORECASE,
            ):
                continue

        # Trigger-only lines must look like real directives, not casual / marketing language.
        # "please feel free", "please find", "please visit" are NOT action items for Justin.
        if has_trigger and not has_name:
            if not re.search(
                r'\b(?:can you|could you|'
                r'please\s+(?:\w+\s+)?(?:do|send|review|confirm|check|let\s+me|'
                r'update|share|schedule|submit|complete|sign|approve|prepare|'
                r'create|follow|reply|respond|help|take|get|add|fix|write|provide|'
                r'reach\s+out|look\s+into|look\s+at|make\s+sure|note\s+that)|'
                r'action\s+item|follow[- ]?up|next\s+step|assigned)\b',
                line, re.IGNORECASE,
            ):
                continue
            if len(line.split()) < 5:
                continue

        due      = _extract_due(ctx)
        priority = _priority(ctx)
        text     = _clean(line)

        if len(text) < 8 or len(text) > 300:
            continue

        key = text.lower()[:80]
        if key in seen_keys:
            continue
        seen_keys.add(key)

        items.append({'text': text, 'due_date': due, 'priority': priority})
        log(f'[rule-extract] [{priority}] {text[:80]}')

    return items

# ── Auto-task adder ────────────────────────────────────────────────────────────
def add_auto_task(item, source, source_detail):
    text     = (item.get('text') or '').strip()
    if not text:
        return
    due_date = item.get('due_date') or None
    priority = item.get('priority', 'medium')
    if priority not in ('high', 'medium', 'low'):
        priority = 'medium'

    today_str = datetime.date.today().isoformat()
    col       = 'longterm' if (due_date and due_date > today_str) else 'today'

    tasks = load_tasks()
    # Deduplicate — skip if same text already exists in either column
    all_texts = {t['text'].lower() for col_ in ('today', 'longterm') for t in tasks.get(col_, [])}
    if text.lower() in all_texts:
        log(f'[auto-add] duplicate skipped: {text[:60]}')
        return

    task = {
        'id':           uid(),
        'text':         text,
        'priority':     priority,
        'due':          due_date,
        'done':         False,
        'created':      int(datetime.datetime.now().timestamp() * 1000),
        'auto':         True,
        'source':       source,
        'sourceDetail': source_detail,
    }
    tasks[col].append(task)
    save_tasks(tasks)
    log(f'[auto-add] [{source} → {col}] {text[:80]}')

# ── Ingest queue helper ────────────────────────────────────────────────────────
def extract_text_from_file(filename, data_bytes):
    """Extract plain text from uploaded file bytes. Supports .txt, .md, .pdf, .docx."""
    ext = pathlib.Path(filename).suffix.lower()

    if ext in ('.txt', '.md'):
        try:
            return data_bytes.decode('utf-8')
        except UnicodeDecodeError:
            return data_bytes.decode('latin-1')

    elif ext == '.pdf':
        if not _PDF_OK:
            raise ValueError('pdfplumber not installed — run: pip3 install pdfplumber')
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
            f.write(data_bytes)
            tmp = pathlib.Path(f.name)
        try:
            parts = []
            with _pdfplumber.open(str(tmp)) as pdf:
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        parts.append(t)
            return '\n\n'.join(parts)
        finally:
            tmp.unlink(missing_ok=True)

    elif ext == '.docx':
        if not _DOCX_OK:
            raise ValueError('python-docx not installed — run: pip3 install python-docx')
        with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as f:
            f.write(data_bytes)
            tmp = pathlib.Path(f.name)
        try:
            doc = _docx_module.Document(str(tmp))
            paras = [p.text for p in doc.paragraphs if p.text.strip()]
            return '\n\n'.join(paras)
        finally:
            tmp.unlink(missing_ok=True)

    elif ext == '.doc':
        raise ValueError(
            '.doc (old Word 97 format) is not directly supported. '
            'Please re-save as .docx or export as PDF, then upload again.'
        )

    else:
        raise ValueError(f'Unsupported file type "{ext}". Use .txt, .md, .pdf, or .docx.')


def queue_for_ingestion(text, source=''):
    """Write text to the ingest queue for AI-powered extraction by scan-companion.py."""
    INGEST_QUEUE_DIR.mkdir(exist_ok=True)
    ts      = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    uid_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    path    = INGEST_QUEUE_DIR / f'{ts}_{uid_str}.json'
    path.write_text(json.dumps({
        'text':      text,
        'source':    source,
        'queued_at': datetime.datetime.now().isoformat(),
    }, ensure_ascii=False))
    log(f'[ingest queue] {path.name} | {len(text)} chars | {source[:60]}')

# ── Calendar / notification email filter ──────────────────────────────────────
def _is_calendar_email(subject, sender):
    """Return True for calendar invites, acceptances, and automated emails to skip."""
    s = subject.lower()
    # Google Calendar invite/acceptance subject prefixes
    skip_prefixes = (
        'accepted:', 'declined:', 'tentative:', 'canceled:', 'cancellation:',
        'canceled event:', 'cancelled event:', 'cancellation notice:',
        'tentatively accepted:', 'tentatively declined:',
        'updated invitation', 'updated invitation with note',
        'invitation:', 'forwarded invitation:', 'new event:',
        'proposed new time:', 'new time proposed:', 're: proposed new time:',
        'document shared with you:', 're: document shared with you:',
        'fwd: document shared with you:',
        'shared a file with you:', 're: shared a file with you:',
        'has shared', 'shared with you:',
    )
    if any(s.startswith(p) for p in skip_prefixes):
        return True
    # Also match calendar phrases anywhere in subject (catches Re:/Fwd: reply chains)
    if re.search(r'proposed\s+new\s+time|new\s+time\s+proposed|rescheduled?\b', s):
        return True
    # Self-sent app reminder emails (3pm/9am digests sent to own inbox)
    smtp_user = env('SMTP_USER', '').lower()
    if smtp_user and smtp_user in sender.lower():
        return True
    # Automated / no-reply senders (NOT gemini-notes — those are queued separately)
    sender_lc = sender.lower()
    skip_senders = (
        'calendar-notification@google.com',
        'no-reply@google.com',
        'noreply@',
        'donotreply@',
        'calendar.google.com',
        'atlassian.net',       # Confluence / Jira digests
        'atlassian.com',       # Atlassian notifications
        'notifications@',      # Generic notification senders
        'drive-shares-dm-noreply@google.com',  # Google Drive share emails
    )
    return any(x in sender_lc for x in skip_senders)

# ── Email quote stripper ──────────────────────────────────────────────────────
def _strip_email_quotes(body):
    """Strip quoted reply/forward blocks from an email body.

    Everything from the first Gmail/Outlook reply attribution onwards is
    quoted content from a *previous* email — not new directives for Justin —
    and must be excluded from action-item extraction.

    Handles:
      • Gmail one-liner:  "On Mon, Jan 1 at 12:00 PM Name <email> wrote:"
      • Gmail wrapped:    "On Mon, Jan 1 at 12:00 PM Name <\n  email> wrote:"
      • "> "-prefixed quoted lines (any number, any depth)
      • "---" / "___" signature / quote separators
    """
    # 1. Gmail/Outlook "On … wrote:" attribution — find the earliest occurrence
    m = re.search(
        r'^On\s+\w[^\n]{5,200}(?:\n[^\n]{0,100})?wrote:\s*$',
        body, re.IGNORECASE | re.MULTILINE,
    )
    if m:
        body = body[:m.start()]

    # 2. Strip any remaining ">" quoted lines and blank lines that follow them
    cleaned = []
    prev_was_quote = False
    for line in body.split('\n'):
        if re.match(r'^>+', line.strip()):
            prev_was_quote = True
            continue
        if prev_was_quote and not line.strip():
            continue          # drop blank lines immediately after a quote block
        prev_was_quote = False
        cleaned.append(line)
    body = '\n'.join(cleaned)

    # 3. Stop at "---" / "___" separator (email signature delimiter)
    m = re.search(r'\n[-_]{3,}\s*\n', body)
    if m:
        body = body[:m.start()]

    return body.strip()

# ── Calendar body detector ────────────────────────────────────────────────────
def _is_calendar_body(body):
    """Return True if body looks like a calendar/meeting notification,
    even when it comes from a human sender (e.g. Google's 'proposed new time' replies)."""
    _CAL_PATTERNS = [
        r'\bOrganizer\s*:',
        r'\bWhen\s*:\s+\S',
        r'\bWhere\s*:\s+\S',
        r'\bJoin\s+(?:Zoom|Google\s+Meet|the\s+meeting|Teams)\b',
        r'(?:zoom\.us/j/|meet\.google\.com/|teams\.microsoft\.com)',
        r'\bVideo\s+call\s+link\b',
        r'\bProposed\s+new\s+time\b',
        r'\bConference\s+(?:ID|room|call)\b',
        r'\bDial-?in\b.*\bnumber\b',
        r'\bGuests\s+can\b',
        r'\bGoing\?\s+(?:Yes|No|Maybe)\b',
    ]
    hits = sum(1 for p in _CAL_PATTERNS if re.search(p, body, re.IGNORECASE))
    return hits >= 2

# ── Email scanner ─────────────────────────────────────────────────────────────
def _decode_header_val(val):
    if not val:
        return ''
    parts = email_decode_header(val)
    out   = []
    for part, enc in parts:
        if isinstance(part, bytes):
            out.append(part.decode(enc or 'utf-8', errors='replace'))
        else:
            out.append(str(part))
    return ''.join(out)

def _email_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == 'text/plain' and part.get('Content-Disposition') != 'attachment':
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or 'utf-8', errors='replace')
                except Exception:
                    pass
        for part in msg.walk():
            if part.get_content_type() == 'text/html':
                try:
                    html = part.get_payload(decode=True).decode(
                        part.get_content_charset() or 'utf-8', errors='replace')
                    return re.sub(r'<[^>]+>', ' ', html)
                except Exception:
                    pass
    else:
        try:
            return msg.get_payload(decode=True).decode(
                msg.get_content_charset() or 'utf-8', errors='replace')
        except Exception:
            pass
    return ''

def scan_email():
    host = env('IMAP_HOST') or env('SMTP_HOST', '').replace('smtp.', 'imap.')
    user = env('IMAP_USER') or env('SMTP_USER')
    pwd  = env('IMAP_PASS') or env('SMTP_PASS')
    if not all([host, user, pwd]):
        return
    try:
        mail = imaplib.IMAP4_SSL(host, 993)
        mail.login(user, pwd)
        # Open read-write so we can restore the \Seen flag if Gmail ignores PEEK
        mail.select('INBOX', readonly=False)
        # Search by date — processed.json is the sole tracker of what we've handled.
        since = (datetime.datetime.now() - datetime.timedelta(days=2)).strftime('%d-%b-%Y')
        _, data = mail.search(None, f'SINCE {since}')
        nums = data[0].split()
        if not nums:
            mail.logout()
            return
        log(f'[email scan] {len(nums)} message(s) in last 2 days to check')
        for num in nums[-50:]:
            try:
                # Record whether the email was unread BEFORE we touch it
                _, flag_data = mail.fetch(num, '(FLAGS)')
                was_unseen = (flag_data and flag_data[0] and
                              b'\\Seen' not in flag_data[0])

                _, msg_data = mail.fetch(num, '(BODY.PEEK[])')

                # Gmail sometimes marks as read despite PEEK — restore if needed
                if was_unseen:
                    mail.store(num, '-FLAGS', '\\Seen')
                msg    = email_lib.message_from_bytes(msg_data[0][1])
                msg_id = msg.get('Message-ID', '').strip()
                if not msg_id or is_processed(f'email_{msg_id}'):
                    continue
                subject = _decode_header_val(msg.get('Subject', '(no subject)'))
                sender  = _decode_header_val(msg.get('From', ''))
                if _is_calendar_email(subject, sender):
                    log(f'[email scan] Skipping calendar/auto: {subject[:55]}')
                    mark_processed(f'email_{msg_id}')
                    continue
                # Gemini meeting-notes emails: queue for AI extraction, skip rule-based
                if 'gemini-notes@google.com' in sender.lower():
                    body = _email_body(msg)
                    queue_for_ingestion(
                        f'{subject}\n\n{body}',
                        f'Gemini Notes: {subject[:80]}'
                    )
                    mark_processed(f'email_{msg_id}')
                    log(f'[email scan] Queued Gemini notes: {subject[:55]}')
                    continue
                # Internal broadcast/summary emails: too contextual for rule-based,
                # route to AI ingest queue so Claude can judge intent properly
                _BROADCAST_SUBJECTS = (
                    'summary', 'alignment', 'recap', 'readout',
                    'highlights', 'debrief', 'status update',
                )
                subject_lc = subject.lower()
                if ('@pactum.com' in sender.lower() and
                        any(kw in subject_lc for kw in _BROADCAST_SUBJECTS)):
                    body = _email_body(msg)
                    queue_for_ingestion(
                        f'From: {sender}\nSubject: {subject}\n\n{body}',
                        f'Email: {subject[:80]}'
                    )
                    mark_processed(f'email_{msg_id}')
                    log(f'[email scan] Queued internal broadcast: {subject[:55]}')
                    continue
                body = _email_body(msg)

                # Skip emails whose body looks like a calendar notification,
                # even when sent from a real person (e.g. Google Calendar
                # "proposed new time" replies have a human From: address).
                if _is_calendar_body(body):
                    log(f'[email scan] Skipping calendar body: {subject[:55]}')
                    mark_processed(f'email_{msg_id}')
                    continue

                # ── External vs internal routing ──────────────────────────
                # Rule-based extraction is tuned for internal @pactum.com mail.
                # External emails (cold outreach, marketing, vendor mail) produce
                # too many false positives, so route them to the AI ingest queue
                # instead — Claude judges intent far more accurately.
                _sender_domain_m = re.search(r'@([\w.\-]+)', sender)
                _sender_domain   = _sender_domain_m.group(1).lower() if _sender_domain_m else ''
                _is_internal     = 'pactum.com' in _sender_domain

                if not _is_internal:
                    queue_for_ingestion(
                        f'From: {sender}\nSubject: {subject}\n\n{body}',
                        f'Email: {subject[:80]}'
                    )
                    mark_processed(f'email_{msg_id}')
                    log(f'[email scan] Queued external email for AI: {subject[:55]}')
                    continue

                # Internal @pactum.com email — rule-based extraction.
                # NOTE: we pass only the body (not the From:/Subject: headers)
                # because header lines create false-positive name matches
                # (e.g. "From: Justin Miller <...>" or "Subject: ... Justin ..."
                # gets extracted as a task).
                # Strip quoted reply/forward blocks so we only examine NEW content.
                log(f'[email scan] Checking: {subject[:60]}')
                _sender_display = sender.split('<')[0].strip() or sender
                body_new = _strip_email_quotes(body)
                for item in extract_action_items('email', body_new):
                    add_auto_task(item, 'email',
                                  f'{subject[:60]} — {_sender_display}')
                mark_processed(f'email_{msg_id}')
            except Exception as e:
                log(f'[email scan] Error on message: {e}')
        mail.close()
        mail.logout()
    except Exception as e:
        log(f'[email scan error] {type(e).__name__}: {e}')

# ── Slack scanner ─────────────────────────────────────────────────────────────
def scan_slack():
    token   = env('SLACK_USER_TOKEN')
    user_id = env('SLACK_USER_ID', 'U0A88TGB91T')
    if not token:
        return

    scan_mins = int(env('SCAN_INTERVAL_MINUTES', '5')) + 2
    since_ts  = (datetime.datetime.now() - datetime.timedelta(minutes=scan_mins)).timestamp()
    seen_ts   = set()

    for query in [f'<@{user_id}>']:
        url = ('https://slack.com/api/search.messages?'
               + urllib.parse.urlencode({'query': query, 'count': 50,
                                         'sort': 'timestamp', 'sort_dir': 'desc'}))
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                if not data.get('ok'):
                    log(f'[slack scan] API error: {data.get("error")}')
                    continue
                for msg in data.get('messages', {}).get('matches', []):
                    ts = msg.get('ts', '')
                    if not ts or float(ts) < since_ts or ts in seen_ts:
                        continue
                    seen_ts.add(ts)
                    pid = f'slack_{ts}'
                    if is_processed(pid):
                        continue
                    channel = msg.get('channel', {}).get('name', 'dm')
                    sender  = msg.get('username', 'someone')
                    text    = msg.get('text', '')
                    content = f'From: {sender} in #{channel}\n\n{text}'
                    log(f'[slack scan] Checking message from #{channel}')
                    for item in extract_action_items('Slack message', content):
                        add_auto_task(item, 'slack', f'#{channel}')
                    mark_processed(pid)
        except Exception as e:
            log(f'[slack scan error] {type(e).__name__}: {e}')

# ── Gong scanner ──────────────────────────────────────────────────────────────
def scan_gong():
    key    = env('GONG_API_KEY')
    secret = env('GONG_API_SECRET')
    base   = env('GONG_BASE_URL', 'https://us-11498.api.gong.io')
    if not all([key, secret]):
        return

    creds   = base64.b64encode(f'{key}:{secret}'.encode()).decode()
    headers = {'Authorization': f'Basic {creds}', 'Content-Type': 'application/json'}
    since   = (datetime.datetime.utcnow() - datetime.timedelta(hours=25)).strftime('%Y-%m-%dT%H:%M:%SZ')

    req = urllib.request.Request(f'{base}/v2/calls?fromDateTime={since}', headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            calls = json.loads(resp.read()).get('calls', [])
            log(f'[gong scan] {len(calls)} recent call(s)')
            for call in calls:
                call_id = call.get('id')
                pid     = f'gong_{call_id}'
                if is_processed(pid):
                    continue
                title = call.get('title', 'Untitled call')
                t_req = urllib.request.Request(
                    f'{base}/v2/calls/{call_id}/transcript', headers=headers)
                try:
                    with urllib.request.urlopen(t_req, timeout=30) as t_resp:
                        utterances = json.loads(t_resp.read()).get('transcript', [])
                        lines = [
                            f'{u.get("speakerName","?")}:'
                            f' {" ".join(s.get("text","") for s in u.get("sentences",[]))}'
                            for u in utterances
                        ]
                        transcript = '\n'.join(lines)
                        if transcript:
                            log(f'[gong scan] Checking transcript: {title}')
                            for item in extract_action_items('meeting call transcript', transcript):
                                add_auto_task(item, 'gong', title)
                except Exception as e:
                    log(f'[gong transcript error] {type(e).__name__}: {e}')
                mark_processed(pid)
    except Exception as e:
        log(f'[gong scan error] {type(e).__name__}: {e}')

# ── Scanner orchestrator ──────────────────────────────────────────────────────
_scan_lock   = threading.Lock()
_scan_status = {'state': 'idle', 'last': None, 'next': None}

def run_scan(manual=False):
    if not _scan_lock.acquire(blocking=False):
        log('[scanner] Already running, skipping')
        return
    try:
        _scan_status['state'] = 'running'
        log(f'[scanner] {"Manual" if manual else "Scheduled"} scan starting…')
        threads = [
            threading.Thread(target=scan_email, daemon=True),
            threading.Thread(target=scan_slack, daemon=True),
            threading.Thread(target=scan_gong,  daemon=True),
        ]
        for t in threads: t.start()
        for t in threads: t.join(timeout=90)
        _scan_status['last']  = datetime.datetime.now().isoformat()
        _scan_status['state'] = 'idle'
        log('[scanner] Scan complete')
    finally:
        _scan_lock.release()
    if not manual:
        _schedule_scan()

def _schedule_scan():
    interval = int(env('SCAN_INTERVAL_MINUTES', '5'))
    nxt      = (datetime.datetime.now() + datetime.timedelta(minutes=interval)).isoformat()
    _scan_status['next'] = nxt
    log(f'[scanner] Next scan in {interval} minute(s)')
    threading.Timer(interval * 60, run_scan).start()

def start_scanners():
    """Called at startup — first scan after 10 seconds, then on interval."""
    log('[scanner] Starting scanner (first scan in 10s)')
    threading.Timer(10, run_scan).start()

# ── Reminder notifications ────────────────────────────────────────────────────
def send_email(subject, body):
    host = env('SMTP_HOST')
    user = env('SMTP_USER')
    pwd  = env('SMTP_PASS')
    to   = env('REMINDER_EMAIL')
    if not all([host, user, pwd, to]):
        log(f'[email skip] {subject}')
        return
    port   = int(env('SMTP_PORT', '587'))
    secure = env('SMTP_SECURE', 'false').lower() == 'true'
    frm    = env('SMTP_FROM', user)
    msg    = MIMEText(body)
    msg['Subject'] = subject
    msg['From']    = frm
    msg['To']      = to
    try:
        ctx = ssl.create_default_context()
        if secure:
            with smtplib.SMTP_SSL(host, port, context=ctx) as s:
                s.login(user, pwd); s.send_message(msg)
        else:
            with smtplib.SMTP(host, port) as s:
                s.ehlo(); s.starttls(context=ctx); s.login(user, pwd); s.send_message(msg)
        log(f'[email sent] {subject}')
    except smtplib.SMTPAuthenticationError as e:
        log(f'[email error] Auth failed — check SMTP_USER/SMTP_PASS: {e}')
    except Exception as e:
        log(f'[email error] {type(e).__name__}: {e}')

def send_slack(text):
    webhook = env('SLACK_WEBHOOK')
    if not webhook:
        log(f'[slack skip] {text[:60]}')
        return
    mention   = SLACK_MENTION
    full_text = f'{mention} {text}' if mention else text
    try:
        payload = json.dumps({'text': full_text}).encode()
        req = urllib.request.Request(
            webhook, data=payload,
            headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=10)
        log('[slack sent]')
    except Exception as e:
        log(f'[slack error] {type(e).__name__}: {e}')

def notify(subject, text):
    t1 = threading.Thread(target=send_email, args=(subject, text), daemon=True)
    t2 = threading.Thread(target=send_slack, args=(text,), daemon=True)
    t1.start(); t2.start()

# ── Cron reminders ────────────────────────────────────────────────────────────
def three_pm_reminder():
    tasks      = load_tasks()
    incomplete = [t for t in tasks.get('today', []) if not t.get('done')]
    if incomplete:
        lines   = '\n'.join(f"  • [{t['priority'].upper()}] {t['text']}" for t in incomplete)
        subject = f"⏰ 3pm: {len(incomplete)} daily task(s) still open"
        body    = f"You have {len(incomplete)} unfinished task(s) today:\n\n{lines}\n\nGet 'em done!"
        log(f'[3pm reminder] notifying about {len(incomplete)} task(s)')
        notify(subject, body)
    else:
        log('[3pm reminder] all tasks complete — no reminder sent')
    schedule_three_pm()

def nine_am_reminder():
    tasks    = load_tasks()
    today    = datetime.date.today()
    triggers = {10, 5, 3, 1}
    found    = 0
    for t in tasks.get('longterm', []):
        if t.get('done') or not t.get('due'):
            continue
        try:
            days_left = (datetime.date.fromisoformat(t['due']) - today).days
        except Exception:
            continue
        if days_left not in triggers:
            continue
        subject = f"📅 {days_left}d left: \"{t['text']}\""
        body    = (f"Long-term task due in {days_left} day(s):\n\n"
                   f"  Task:     {t['text']}\n"
                   f"  Priority: {t['priority']}\n"
                   f"  Due:      {t['due']}\n\nTime to make progress!")
        log(f'[due-date reminder] {days_left}d → "{t["text"]}"')
        notify(subject, body)
        found += 1
    if not found:
        log('[9am reminder] no due-date thresholds hit today')
    schedule_nine_am()

def _seconds_until(hour, minute=0):
    now    = datetime.datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= target:
        target += datetime.timedelta(days=1)
    return (target - now).total_seconds()

def schedule_three_pm():
    delay = _seconds_until(15, 0)
    log(f'[scheduler] next 3pm reminder in {delay/3600:.1f}h')
    threading.Timer(delay, three_pm_reminder).start()

def schedule_nine_am():
    delay = _seconds_until(9, 0)
    log(f'[scheduler] next 9am reminder in {delay/3600:.1f}h')
    threading.Timer(delay, nine_am_reminder).start()

# ── HTTP server ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        if self.path.startswith('/api/'):
            log(f'  {self.command} {self.path}')

    def _body(self):
        length = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(length) if length else b''

    def _send(self, code, body, ct='application/json'):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ct)
        self.send_header('Content-Length', len(data))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj))

    def _static(self, path):
        fpath = PUBLIC / path.lstrip('/')
        if fpath.is_dir():
            fpath = fpath / 'index.html'
        if not fpath.exists():
            self._send(404, b'Not found', 'text/plain')
            return
        ext_map = {'.html': 'text/html', '.js': 'application/javascript',
                   '.css': 'text/css', '.ico': 'image/x-icon'}
        self._send(200, fpath.read_bytes(), ext_map.get(fpath.suffix, 'application/octet-stream'))

    def do_GET(self):
        p = self.path.split('?')[0]
        if p == '/api/tasks':
            self._json(load_tasks())
        elif p == '/api/config':
            self._json({
                'email':       bool(env('SMTP_HOST') and env('SMTP_USER') and
                                    env('SMTP_PASS') and env('REMINDER_EMAIL')),
                'slack':       bool(env('SLACK_WEBHOOK')),
                'ai':          True,   # rule-based extraction — no API key needed
                'emailScan':   bool(env('SMTP_HOST') and env('SMTP_USER') and env('SMTP_PASS')),
                'slackScan':   bool(env('SLACK_USER_TOKEN')),
                'gongScan':    bool(env('GONG_API_KEY') and env('GONG_API_SECRET')),
                'scanStatus':  _scan_status,
            })
        else:
            self._static(p if p != '/' else '/index.html')

    def do_POST(self):
        p = self.path.split('?')[0]
        if p == '/api/tasks':
            try:
                save_tasks(json.loads(self._body()))
                self._json({'ok': True})
            except Exception as e:
                self._json({'error': str(e)}, 500)
        elif p == '/api/test-reminders':
            threading.Thread(
                target=notify,
                args=('🔔 Test Reminder', 'Your Todo app reminders are working correctly!'),
                daemon=True,
            ).start()
            self._json({'ok': True})
        elif p == '/api/scan':
            if _scan_lock.locked():
                self._json({'ok': False, 'msg': 'Scan already in progress'})
            else:
                # Write trigger file for scan-companion.py (Claude Code MCP bridge)
                # That script watches for this file and runs the full AI-powered Slack scan.
                try:
                    SCAN_TRIGGER.write_text(datetime.datetime.now().isoformat())
                except Exception as e:
                    log(f'[scan trigger] Could not write trigger: {e}')
                # Also run the built-in rule-based scan (handles email when no companion running)
                threading.Thread(target=run_scan, kwargs={'manual': True}, daemon=True).start()
                companion = (BASE_DIR / '.scan-companion-alive').exists()
                msg = 'Scan started — Claude companion active' if companion else \
                      'Scan started (run scan-companion.py in a terminal for AI Slack scan)'
                self._json({'ok': True, 'msg': msg})
        elif p == '/api/ingest':
            try:
                body_data = json.loads(self._body())
                text   = (body_data.get('text') or '').strip()
                source = (body_data.get('source') or 'Manual upload').strip()
                if not text:
                    self._json({'ok': False, 'msg': 'No text provided'}, 400)
                    return
                queue_for_ingestion(text, source)
                companion = (BASE_DIR / '.scan-companion-alive').exists()
                self._json({
                    'ok':       True,
                    'msg':      'Queued for AI extraction',
                    'companion': companion,
                })
            except Exception as e:
                log(f'[ingest error] {e}')
                self._json({'ok': False, 'msg': str(e)}, 500)
        elif p == '/api/ingest/extract':
            # Extract text from an uploaded document (PDF, DOCX, TXT, MD)
            # Body: {filename: str, data: base64-encoded file bytes}
            try:
                body_data  = json.loads(self._body())
                filename   = (body_data.get('filename') or 'file').strip()
                b64_data   = body_data.get('data', '')
                if not b64_data:
                    self._json({'ok': False, 'msg': 'No file data provided'}, 400)
                    return
                file_bytes = base64.b64decode(b64_data)
                text = extract_text_from_file(filename, file_bytes)
                if not text.strip():
                    self._json({'ok': False,
                                'msg': 'No text could be extracted from this file.'}, 422)
                    return
                self._json({'ok': True, 'text': text,
                            'filename': filename, 'chars': len(text)})
            except Exception as e:
                log(f'[extract error] {e}')
                self._json({'ok': False, 'msg': str(e)}, 400)
        else:
            self._send(404, b'Not found', 'text/plain')

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    INGEST_QUEUE_DIR.mkdir(exist_ok=True)   # ensure queue dir exists
    log(f'✅  Todo app  →  http://localhost:{PORT}')
    log(f'   Email   : {"✓" if env("SMTP_HOST") else "✗ (see .env)"}')
    log(f'   Slack   : {"✓" if env("SLACK_WEBHOOK") else "✗ (see .env)"}')
    log(f'   AI      : ✓ rule-based extraction (no API key needed)')
    log(f'   Scanners: email={"✓" if env("SMTP_USER") else "✗"} slack={"✓" if env("SLACK_USER_TOKEN") else "✗"} gong={"✓" if env("GONG_API_KEY") else "✗"}')
    log(f'   Crons   : daily tasks @ 3:00pm | long-term @ 9:00am')

    schedule_three_pm()
    schedule_nine_am()
    start_scanners()

    server = HTTPServer(('', PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log('Stopped.')
