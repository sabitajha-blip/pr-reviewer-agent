# PR Review AI Agent (Polling Edition)

A GitHub PR reviewer powered by Claude that **learns from your team's reviews**.
Polls GitHub every N minutes (default 5) — no webhook, no public URL, no ngrok.
Just run it on your laptop or any small server.

Memory is stored as markdown files inside the repo's `.agent-memory/` folder,
so every memory update is a git commit you can audit, edit, or revert.

## How it works

```
   every 5 minutes:
     1. List open PRs    → review any new/updated ones (Claude + memory context)
     2. List comments    → learn from any new peer comments (commit to memory)
     3. List merged PRs  → log accepted changes
```

State of what's been processed lives in `state.json` so you can stop and restart
the agent without re-reviewing things.

## Setup

### 1. Create the test repo

1. Sign in to GitHub.
2. Create a new public repo, e.g. `pr-review-agent-test`.
3. Add a `README.md` and a sample Python file so there's something to PR against.

### 2. Bootstrap the memory folder in your test repo

```
.agent-memory/
├── rules.md      ← copy from app/memory_templates/rules.md
├── index.md      ← copy from app/memory_templates/index.md
├── patterns/     ← empty (add .gitkeep)
├── comments/     ← empty (add .gitkeep)
└── feedback/     ← empty (add .gitkeep)
```

Commit and push this to `main`.

### 3. Get your tokens

- **GitHub PAT**: https://github.com/settings/tokens → Generate (classic).
  Scopes: `repo`. Save the token.
- **Anthropic API key**: https://console.anthropic.com/

### 4. Run the agent

```bash
cd pr-review-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env — set GITHUB_TOKEN, ANTHROPIC_API_KEY, TARGET_REPO

python -m app.poller
```

You should see:
```
🤖 PR Review Agent starting
    Target repo:     yourname/pr-review-agent-test
    Poll interval:   300s
    Auth user:       yourname
─── Poll tick at 2026-05-23T10:00:00 ───
Found 0 open PR(s)
─── Tick done. Sleeping 300s ───
```

Leave it running. Now create a test PR (see Testing below) and on the next tick
the agent will review it.

### 5. (Optional) Run faster while testing

```bash
POLL_INTERVAL_SECONDS=30 python -m app.poller
```

30-second polls let you iterate quickly. Switch back to 300s for normal use.

## Testing

### Test 1 — does the bot review a PR?

In your test repo:
```bash
git checkout -b test-1
# Add a file with deliberate problems:
cat > buggy.py << 'EOF'
def fetch_user(user_id):
    try:
        print(f"Fetching user {user_id}")
        return execute(f"SELECT * FROM users WHERE id={user_id}")
    except:
        pass
EOF
git add buggy.py && git commit -m "Add fetch_user"
git push -u origin test-1
# Open a PR on GitHub
```

Within one poll cycle the agent will post an AI Review comment flagging the
`print()`, bare `except:`, and SQL injection.

### Test 2 — does the bot learn from peer comments?

On the PR, leave an inline comment like:

> "We always use type hints on function arguments in this codebase."

On the next poll tick, you'll see in the agent's terminal:
```
Analyzing comment 12345 by yourname on buggy.py
✓ Stored new memory: .agent-memory/patterns/20260523-use-type-hints.md
```

Pull from `main` in your test repo — you'll see a new commit by the agent's
account adding the memory file.

### Test 3 — does the bot use what it learned?

Open a second PR with code that violates the new rule:
```bash
git checkout main && git pull
git checkout -b test-2
echo 'def discount(price, percentage): return price * (1 - percentage/100)' > calc.py
git add calc.py && git commit -m "Add discount calculator"
git push -u origin test-2
# Open PR #2
```

The agent's review on PR #2 should now flag the missing type hints, citing the
rule it learned from PR #1.

## Running it as a background service

For ongoing use you probably don't want to keep a terminal open. Options:

### Option A: Run on a small VM (Railway, Render, Fly.io)
Deploy as a worker (not a web service). Set the env vars in the host's
dashboard and run `python -m app.poller` as the start command.

### Option B: systemd on Linux
```
[Unit]
Description=PR Review Agent
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/you/pr-review-agent
EnvironmentFile=/home/you/pr-review-agent/.env
ExecStart=/home/you/pr-review-agent/.venv/bin/python -m app.poller
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
```

### Option C: macOS launchd / Windows Task Scheduler
Same idea — schedule `python -m app.poller` to start at login and restart on failure.

## Troubleshooting

- **"GITHUB_TOKEN" KeyError on startup** — you forgot to fill in `.env`.
- **"Bad credentials"** — PAT is wrong or expired. Regenerate.
- **"Not Found" when posting comment** — your PAT doesn't have `repo` scope, OR
  the repo name in `TARGET_REPO` is wrong (must be `owner/name`).
- **Agent never reviews a PR** — check `state.json`. If the PR number is already
  in `reviewed_pr_heads` with the current head sha, it thinks it already did it.
  Delete `state.json` to force a clean slate.
- **Bot reviews its own commits to .agent-memory/** — shouldn't happen because
  we filter by `bot_username`, but if the PAT belongs to your personal account
  the bot may pick up your own future PRs. Use a separate "bot" GitHub account
  for production.

## Roadmap

- [ ] Promote rules from `patterns/` to `rules.md` after N occurrences
- [ ] Inline review comments (not just summary)
- [ ] Per-poll change detection via ETag for fewer API calls
- [ ] Reactions on bot comments → feeds into memory weighting
