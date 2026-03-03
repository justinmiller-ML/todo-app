# AI To-Do App — Installation Guide

This app scans your email, Slack messages, and meeting notes to automatically find action items assigned to you and surface them in a simple task list you open in your browser. Once set up, it runs silently in the background and starts automatically every time you log in.

**Time required:** 30–45 minutes, mostly spent collecting credentials from Google and Slack before you run anything.

**Requirements:** Mac only. A Google Workspace (Gmail) account with 2-Step Verification turned on.

---

## Before You Start: Collect Your Credentials

Do this part first, away from the terminal. You'll need three things in hand before running the installer. Having them ready means setup takes 5 minutes instead of 30.

---

### Credential 1: Gmail App Password

The app needs to read your email to find action items. Google requires a special "App Password" for this — it's separate from your regular Google password and only gives access to your inbox. Your real password is never used.

**Steps:**

1. Go to [myaccount.google.com](https://myaccount.google.com)
2. Click **Security** in the left menu
3. Under "How you sign in to Google," click **2-Step Verification** (it must be turned on — if it says "Off," turn it on first)
4. Scroll to the very bottom of that page and click **App passwords**
   - If you don't see "App passwords," it means 2-Step Verification isn't fully active yet
5. Under "App name," type anything — for example: `Todo App`
6. Click **Create**
7. Google shows you a 16-character password like `abcd efgh ijkl mnop`

**Save this password somewhere safe right now — Google only shows it once.** You can ignore the spaces; the installer accepts it either way.

---

### Credential 2: Slack User OAuth Token

This lets the app scan your Slack DMs and channels for action items. You'll create a small private "app" inside Slack that only you can see — it sounds technical but takes about 5 minutes.

**Steps:**

1. Go to [api.slack.com/apps](https://api.slack.com/apps) (log in with your Pactum Slack account if prompted)
2. Click **Create New App** (top right)
3. Choose **From scratch**
4. Name it anything — `Todo Scanner` works fine
5. Under "Pick a workspace to develop your app in," select **Pactum**
6. Click **Create App**
7. In the left menu, click **OAuth & Permissions**
8. Scroll down to the **User Token Scopes** section (not "Bot Token Scopes" — the one below it)
9. Click **Add an OAuth Scope** and type `search:read`, then select it
10. Scroll back to the top of the page and click **Install to Workspace**
11. Click **Allow** on the confirmation screen
12. You're taken back to the OAuth & Permissions page — copy the **User OAuth Token** at the top. It starts with `xoxp-` and is very long.

**Save this token somewhere safe.** Like the Gmail password, treat it like a password — don't share it.

---

### Credential 3: Your Slack User ID

This is how Slack identifies you internally. It looks like `U0A88TGB91T`.

**Steps:**

1. Open the Slack desktop app or web app
2. Click your **profile picture** in the top right corner
3. Click **Profile**
4. In the profile panel that opens, click the **three dots (...)** near the top
5. Click **Copy member ID**

That's it. Paste it somewhere — you'll need it in the setup wizard.

---

## Part 1: Install Claude Code

The app uses Claude Code as its AI engine for scanning and understanding your notes. You need it installed and logged in before running the app installer.

**Steps:**

1. Go to [claude.ai/download](https://claude.ai/download) and download Claude for Mac
2. Open the downloaded file and drag Claude to your Applications folder
3. Open Claude from your Applications folder
4. Claude will ask you to log in — use your work email or create an Anthropic account
5. Once logged in, you should see the Claude Code interface open

**Leave Claude Code open** — the next step happens inside it.

---

## Part 2: Connect Slack to Claude Code

Claude Code uses Slack through a connector called an MCP server. The app installer can find this automatically, but you need to connect it first.

**Steps:**

1. In Claude Code, look for a small plug icon or search for integrations (this varies by version — look for "Integrations" or "MCP" in the settings menu)
2. Find **Slack** in the list and connect it using your Pactum Slack account
3. When Slack asks to authorize access, click **Allow**

Once connected, Claude Code will be able to read your Slack messages on your behalf. The app installer will detect this connection automatically in the next step.

---

## Part 3: Run the Installer

This is the main event. Open Terminal (it's in your Applications → Utilities folder, or press `Command + Space` and type "Terminal"), then paste the following command and press Enter:

```
bash <(curl -fsSL https://raw.githubusercontent.com/justinmiller-ML/todo-app/main/install.sh)
```

The installer will walk you through 8 steps automatically. Here's what to expect at each one:

---

**Step 1 — Checking prerequisites**
The installer checks that your Mac has Python 3 installed (it almost certainly does). If it says Python 3 isn't found, visit [python.org](https://python.org) and download the latest version, then re-run the command above.

---

**Step 2 — Locating Claude CLI binary**
The installer finds Claude Code on your machine. If Claude Code is installed and you've opened it at least once, this should find it automatically. If it says "Claude CLI not found," open Claude Code, wait a moment, then press Enter to retry.

---

**Step 3 — Discovering Slack MCP server UUID**
The installer looks for your Slack connection inside Claude Code. If you completed Part 2 above, it finds this automatically and shows a confirmation. If it says it couldn't auto-detect, follow the on-screen instructions to find the UUID manually.

---

**Step 4 — Configure credentials**
This is where you enter everything you collected in the "Before You Start" section. The wizard will ask for:

- **Your full name** — type your name exactly as it appears in Slack and email (e.g. `Michael Long`). This is how the app recognizes that a task is assigned to you.
- **Your email address** — your Pactum Google Workspace email
- **SMTP host** — the installer guesses this from your email domain. For Google Workspace addresses, it will say `smtp.gmail.com`. Press Enter to accept.
- **Email App Password** — paste the 16-character password from Credential 1. Nothing will appear as you type — that's normal for password fields.
- **Reminder email address** — where you want task reminder emails sent. Press Enter to use the same address.
- **Slack Webhook URL** — optional. This lets the app send you Slack notifications for reminders. Press Enter to skip for now — you can add it later.
- **Slack User OAuth Token** — paste the `xoxp-...` token from Credential 2. Nothing will appear as you type.
- **Slack User ID** — paste the ID from Credential 3 (looks like `U0A88TGB91T`)

---

**Step 5 — Patching scan-companion.py**
Automatic. The installer configures the AI scanning component with the correct paths for your machine. You don't need to do anything.

---

**Step 6 — Initialising tasks.json**
Automatic. Creates your empty task list.

---

**Step 7 — Installing LaunchAgents**
Automatic. This sets up the app to start automatically every time you log into your Mac. No manual launching needed after this.

---

**Step 8 — Verifying the server**
The installer checks that the app started successfully and opens it in your browser automatically. If you see the task list interface, setup is complete.

---

## Part 4: Verify It's Working

After setup, your browser should have opened to `http://localhost:3000` showing the task list. If it didn't open automatically, open any browser and navigate there manually.

You should see an empty task list with a "Scan Now" button and an "Add Notes" panel. That's the correct starting state.

**Test the email scan:**
1. Click **Scan Now**
2. Wait about 30 seconds
3. If you have emails with action items assigned to you by name, they should start appearing in the Today list

If nothing appears, that's normal on day one — the scanner looks for recent emails and may not find anything right away. It runs automatically in the background every few minutes.

---

## How to Use the App Day-to-Day

- **Open the app:** Go to `http://localhost:3000` in any browser. Bookmark it.
- **Scan Now:** Manually triggers a scan of your email and Slack. Otherwise it runs automatically every few minutes.
- **Add Notes:** Paste meeting notes, a Gemini meeting summary, or any text — the app uses AI to extract action items from it.
- **Today vs. Longterm:** Tasks land in Today by default. Drag them to Longterm if they're not urgent.
- **Clear Done:** Marks completed tasks as done and removes them from the list permanently.

---

## How to Get Updates

When Justin makes improvements to the app, you get them with one command. Open Terminal and run:

```
bash ~/todo-app/update.sh
```

That's it. It pulls the latest version from GitHub and restarts the app automatically. Your tasks and credentials are not affected.

---

## Troubleshooting

**The app isn't running / browser shows "can't connect"**

Open Terminal and run:
```
pkill -9 -f "python3.*server.py"; cd ~/todo-app && python3 server.py >> server.log 2>&1 &
```
Then go to `http://localhost:3000`.

If it still doesn't work, check the log for errors:
```
tail -50 ~/todo-app/server.log
```

---

**Email scan isn't finding tasks**

The most common cause is an incorrect App Password. To re-enter it:
1. Open Terminal
2. Run: `open ~/todo-app/.env` (opens in TextEdit)
3. Find the line that says `SMTP_PASS=` and replace the value with your correct App Password
4. Save the file
5. Restart the app with the command above

---

**Slack scan isn't working**

Check that your `xoxp-` token is correct in the config file (`~/todo-app/.env`, line `SLACK_USER_TOKEN=`). Tokens expire if you reinstall the Slack app or revoke access. If it's expired, go back to [api.slack.com/apps](https://api.slack.com/apps), find your Todo Scanner app, and copy a fresh token.

---

**"Something went wrong" on the Scan Now button**

This usually means the AI companion process (scan-companion) isn't running. It starts automatically on login, but if it's stopped:
1. Open Terminal
2. Run: `python3 ~/todo-app/scan-companion.py &`
3. Leave Terminal open in the background (minimizing is fine)

If the LaunchAgent isn't starting it automatically, re-run `bash ~/todo-app/setup.sh` — it will re-install the auto-start configuration without touching your credentials.

---

**Need to re-run setup or change a credential**

Run `bash ~/todo-app/setup.sh` again. It will ask whether to overwrite your existing config. Choose yes to update credentials, no to skip straight to re-installing the LaunchAgents.
