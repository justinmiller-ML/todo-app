# AI To-Do App — Installation Guide

This app scans your email, Slack messages, and meeting notes to automatically find action items assigned to you and surfaces them in a simple task list you open in your browser. Once set up, it runs silently in the background and starts automatically every time you log in.

**Time required:** 20–30 minutes.

**Requirements:** Mac only. A Google Workspace (Gmail) account with 2-Step Verification turned on.

---

## Before You Start: One Thing to Collect

You need one credential before running the installer — a Gmail App Password. Everything else (including Slack) is handled automatically during setup.

### Gmail App Password

The app needs to read your email to find action items. Google requires a special "App Password" for this — it's separate from your regular Google password and only gives access to your inbox.

**Steps:**

1. Go to [myaccount.google.com](https://myaccount.google.com)
2. Click **Security** in the left menu
3. Under "How you sign in to Google," click **2-Step Verification** — it must be turned on. If it says "Off," turn it on first, then come back here.
4. Scroll to the very bottom of that page and click **App passwords**
   - If you don't see "App passwords," 2-Step Verification isn't fully active yet
5. Under "App name," type anything — for example: `Todo App`
6. Click **Create**
7. Google shows you a 16-character password like `abcd efgh ijkl mnop`

**Save this password now — Google only shows it once.** You can ignore the spaces when pasting it.

---

## Part 1: Install Claude Code

The app uses Claude Code as its AI engine. You need it installed and logged in before running the app installer.

**Steps:**

1. Go to [claude.ai/download](https://claude.ai/download) and download Claude for Mac
2. Open the downloaded file and drag Claude to your Applications folder
3. Open Claude from your Applications folder
4. Log in when prompted — use your work email or create an Anthropic account
5. Leave Claude Code open

---

## Part 2: Connect Slack to Claude Code

The app reads your Slack messages through Claude Code's Slack integration.

**Steps:**

1. In Claude Code, open Settings (gear icon, or `Cmd + ,`)
2. Find the **Integrations** or **MCP** section
3. Find **Slack** in the list and connect it using your Pactum Slack account
4. When Slack asks to authorize access, click **Allow**

Once connected, leave Claude Code running in the background.

---

## Part 3: Run the Installer

Open Terminal (Applications → Utilities → Terminal, or press `Cmd + Space` and type "Terminal"), paste the following, and press Enter:

```
bash <(curl -fsSL https://raw.githubusercontent.com/justinmiller-ML/todo-app/main/install.sh)
```

The installer walks you through 8 steps. Here's what happens at each one:

---

**Step 1 — Checking prerequisites**
Verifies your Mac has Python 3. If it says Python 3 isn't found, download it from [python.org](https://python.org) and re-run the command.

---

**Step 2 — Locating Claude CLI binary**
Finds Claude Code on your machine automatically. If it says "Claude CLI not found," open Claude Code, wait a moment, then press Enter to retry.

---

**Step 3 — Discovering Slack MCP server UUID**
Finds your Slack connection inside Claude Code automatically. If it can't auto-detect, follow the on-screen instructions to locate it manually.

---

**Step 4 — Configure credentials**
The wizard will ask for:

- **Your full name** — type your name exactly as it appears in Slack and email (e.g. `Michael Long`). This is how the app knows which tasks are assigned to you.
- **Your email address** — your Pactum Google Workspace email
- **SMTP host** — press Enter to accept the default (it auto-detects from your email address)
- **Gmail App Password** — your browser will open to the App Passwords page automatically. Create one, copy it, and paste it here. Nothing will appear as you type — that's normal for password fields.
- **Reminder email** — press Enter to use your same email address
- **Slack Webhook URL** — optional, for reminder notifications. Press Enter to skip.

---

**Steps 5–7** — Automatic. The installer configures the AI scanning component, creates your task list, and sets up the app to start automatically at login.

---

**Step 8 — Verifying the server**
The installer confirms the app started and opens it in your browser. If you see the task list interface, you're done.

---

## Part 4: Verify It's Working

After setup, your browser should open to `http://localhost:3000`. If it didn't open automatically, navigate there manually. Bookmark it — that's the app.

You should see an empty task list with a "Scan Now" button and an "Add Notes" panel.

**Test it:** Click **Scan Now** and wait about 30 seconds. If you have recent emails with action items assigned to you by name, they should start appearing. If nothing shows up right away, that's normal — it depends on what's in your inbox.

---

## Day-to-Day Use

- **Open the app:** Go to `http://localhost:3000` in any browser
- **Scan Now:** Triggers a manual scan of email and Slack. Otherwise it runs automatically in the background every few minutes.
- **Add Notes:** Paste meeting notes or a transcript — the app extracts action items from it automatically.
- **Today vs. Longterm:** Tasks land in Today by default. Drag to Longterm if they're not urgent.
- **Clear Done:** Marks completed tasks done and removes them permanently.

---

## Getting Updates

When changes are made to the app, run this in Terminal:

```
bash ~/todo-app/update.sh
```

It pulls the latest version and restarts the app. Your tasks and credentials are not affected.

---

## Troubleshooting

**App isn't running / browser shows "can't connect"**

Open Terminal and run:
```
pkill -9 -f "python3.*server.py"; cd ~/todo-app && python3 server.py >> server.log 2>&1 &
```
Then go to `http://localhost:3000`. To check for errors: `tail -50 ~/todo-app/server.log`

---

**Email scan isn't finding tasks**

Most likely an incorrect App Password. Open Terminal and run:
```
open ~/todo-app/.env
```
Find the `SMTP_PASS=` line, replace the value with your correct App Password, save, then restart the app with the command above.

---

**Slack scan isn't working**

The Slack connection runs through Claude Code's built-in Slack integration. Make sure Claude Code is open and running in the background. If the issue persists, re-run `bash ~/todo-app/setup.sh` — it will reconnect Slack automatically.

---

**"Something went wrong" on Scan Now**

The AI companion process has stopped. In Terminal:
```
python3 ~/todo-app/scan-companion.py &
```
If this keeps happening, re-run `bash ~/todo-app/setup.sh` to reinstall the auto-start configuration.

---

**Need to change a credential**

Run `bash ~/todo-app/setup.sh` again. It will ask whether to overwrite your config. Choose yes to update credentials.
