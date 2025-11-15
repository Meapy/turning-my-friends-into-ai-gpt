# Discord Scraper — ai_friends

This repository contains a canonical Discord message scraper (`discord_scraper.py`) that collects all messages from a specified user in a guild into a JSONL file for downstream processing (for example: training an AI model).

This README is the source of truth for how the project works and how to use it — keep it updated when you change code or behavior.

## Files of interest
- `discord_scraper.py` — canonical CLI scraper. Supports resume via a per-channel state file.
- `requirements.txt` — Python dependencies (discord.py, python-dotenv, nest_asyncio).
- `messages.jsonl` — output file (JSON Lines) with one message per line.
- `.scrape_state.json` — default checkpoint/state file (created when scraping). Keeps last processed message id per channel.
- `discordMessageScraper.ipynb` — notebook reserved for later AI training/data work. Do not use it as the primary scraper.

## Quickstart (Windows cmd)
1. Create and activate a virtual environment inside the project (recommended `.venv`):

```cmd
python -m venv .venv
.venv\\Scripts\\activate
```

2. Install dependencies:

```cmd
pip install -r requirements.txt
```

3. Create a `.env` file in the repository root (or export env vars) with:

```
DISCORD_TOKEN=your_bot_token_here
GUILD_ID=123456789012345678
TARGET_USER_ID=987654321098765432
```

4. Run the scraper (example):

```cmd
python discord_scraper.py --out messages.jsonl --state .scrape_state.json --verbose
```

You can pass `--token`, `--guild`, and `--user` instead of using `.env`.

## Resume behavior (checkpointing)
- The scraper appends each collected message to the output JSONL and records the last processed message ID per channel in the state file (default `.scrape_state.json`).
- On re-run, the script fetches only messages after the saved message ID for each channel, so scraping resumes from where it left off.
- To re-scan a channel from scratch, remove that channel's entry from the state file or delete the state file.
New: per-channel output, concurrency and reliability improvements
- The scraper writes messages into a `messages/` folder under the repository. Each channel gets its own per-channel file named after the channel: `messages/<channel-name>.jsonl`. If a name collision is detected the channel id is appended: `messages/<channel-name>-<channel-id>.jsonl`.
- The checkpoint/state file is stored by default at `messages/.scrape_state.json` (avoids cross-directory permission issues on Windows/OneDrive).
- Use `--concurrency N` to control how many channels are scraped in parallel (default 3). Higher values increase throughput but also the chance of rate-limiting.

Reliability & performance options (new):
- Buffered, batched writes: messages are buffered and flushed in batches to reduce blocking disk I/O and improve throughput.
- Optional async/file-speedups: if `aiofiles` and `ujson` are installed the scraper will use async file writes and faster JSON serialization; the script works correctly without them.
- `--durable-state`: enable fsync when saving the state file (slower but safer). By default fsync is skipped for speed.
- `--precount`: start an optional background precount that estimates how many target-user messages exist across channels; the reporter will show a percent-complete as the estimate updates. Precount is accurate but expensive (it scans history) — a sampling estimator is possible if you prefer a faster approximate percent.
- Rate-limit resilience: the scraper detects 429 responses and retries with backoff. You may still see 429 warnings when Discord rate-limits the history endpoint; the code will wait and resume automatically.
- Hardened state writes: `save_state()` uses a temp file + atomic replace with retries and exponential backoff to reduce transient Windows file-lock errors.
- Graceful shutdown: pressing Ctrl+C will save current progress, flush buffers and close the client cleanly so runs can resume exactly where they left off.

## Output format
Each line in `messages.jsonl` is a JSON object with fields similar to:

{
  "id": <message_id>,
  "content": "message text",
  "author": {"id": <author_id>, "name": "display#1234"},
  "channel": {"id": <channel_id>, "name": "channel-name"},
  "created_at": "ISO timestamp",
  "attachments": ["url1", "url2"]
}

The file is append-only; consider running a dedupe/compaction step before training.

Note: when using per-channel mode the files live under `messages/` (one JSONL per channel); run a dedupe/merge step if you need a single training file.

## Permissions & Discord setup
- In the Discord Developer Portal, enable the MESSAGE_CONTENT (privileged) intent for your bot.
- Invite the bot to the guild with permissions to read message history and view channels.
- Respect privacy and obtain consent before scraping messages.

## Maintenance: keep README as source of truth
- When you change `discord_scraper.py`, immediately update this README with any behavior changes (arguments, state file name, output format, checkpointing frequency, etc.).
- Add short changelog notes or comments in the README to indicate the last update and what changed.

### Git hook to enforce README updates

This repo includes a local git hook and a small checker script to help ensure README.md is updated when code files change.

Files added:
- `.githooks/pre-commit` — a local pre-commit hook that runs the checker.
- `tools/check_readme.py` — examines staged files and fails the commit if code files are changed without README.md.

To enable the local hooks in your repo run:

```cmd
git config core.hooksPath .githooks
```

After running that, the pre-commit hook will run automatically on `git commit`.

If you prefer system-wide enforcement, integrate the checker with your CI pipeline.

## Future improvements (suggested)
- Add `dedupe_compact.py` to compact and dedupe `messages.jsonl` into a final training dataset.
- Add tests for `load_state`/`save_state` and CLI behavior.
- Add a GitHub Action to run linting / basic smoke tests on changes.

## Copilot / automation guidance
Add `.copilot-config.json` in the repo root with a short rule set that instructs assistants and automation to remind and enforce README updates when code changes are made. The file is included in the repository and should be respected by any automation integrated with Copilot that reads local config.

## Troubleshooting
- Event loop errors in notebooks: use `discord_scraper.py` (script). The notebook is not the canonical scraper.
- If you get permission errors, verify bot intents and invite permissions.

---

Changelog
- 2025-11-15 — Added per-channel messages folder, buffered async writes, `--durable-state`, `--precount` (background precount with incremental percent), rate-limit backoff and retries, hardened `save_state()` with retries, and graceful Ctrl+C shutdown.

_Last updated: 2025-11-15_
