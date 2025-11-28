# Copilot & Automation Instructions for this Repository

Purpose: provide clear, actionable instructions for Copilot agents and other automation so they keep the repository consistent, maintain the README as the source of truth, and follow project rules when editing code.

Be consise and specific, do not waffle to much. less code is always better than more code. If something is not working, remove the unnecessary code before adding new code.
innovate, do more than just asked. Be a 10x developer.
clean the code as you go. Make sure there is no stale code left behind. Always keep track of code and try to remove as much code as you add wherever possible.
When making a change, also add context on how the change improves the codebase. use numbers where possible.
keep the code base clean, make new folders & files where needed to seperate concerns.

1) README is the source of truth
- Always update `README.md` when making changes to code that affect usage, CLI flags, behavior, output format, file names, or state/checkpoint semantics.
- Add a one-line changelog entry under the `Changelog` section with date and short summary for each change.

2) Git hook and enforcement
- This repo includes a local pre-commit hook at `.githooks/pre-commit` which runs `tools/check_readme.py`.
- The checker blocks commits that modify code files (`.py`, `.ipynb`) without also staging `README.md`.
- Enable hooks locally with:
  `git config core.hooksPath .githooks`
- As automation, if you modify code in a PR, ensure the PR also includes README.md updates. If running in CI, ensure the check is replicated server-side.

3) Scraper is canonical in `discord_scraper.py`
- Use `discord_scraper.py` as the canonical scraper script. The notebook `discordMessageScraper.ipynb` is reserved for AI/data work only and should not be used as the primary scraper.
- The script supports resume/checkpointing via a per-channel JSON state file (default `.scrape_state.json`).
- The script appends to `messages.jsonl` and records last-processed message IDs per channel.

4) Avoid duplicates
- The scraper pre-loads existing message IDs from `messages.jsonl` and will skip messages already present, unless `--force-rewrite` is passed which rewrites the output.
- If you change duplicate handling behavior, update README and document memory/tradeoffs.

5) Environment & venv
- Use a project-local venv (recommended `.venv`). Document setup steps in README. Do not commit virtualenv files; `.gitignore` excludes `.venv` and `.env`.

6) Data privacy
- Only collect messages when you have permission. The project README contains an ethics section — keep it up-to-date if scraping policy changes.

7) Automation behaviour and messaging
- If an automated assistant modifies code, it must also update README and add a changelog entry. If unable to update README, the assistant should leave an explicit comment explaining why and open an issue to track the missing doc update.
- Use `--verbose` when running scrapers in automated runs for better logs.

8) CI and server-side enforcement
- Prefer adding the README check to CI to ensure server-side enforcement. The local pre-commit hook is a convenience but not a guarantee — CI is authoritative.

9) If you are a human maintainer
- Follow the same rules above. If you merge code that changes behavior without updating README, add a follow-up commit immediately updating README with a changelog entry.

10) Contact / issues
- If unsure about whether a README change is required, create an issue titled `docs: confirm README update` and explain the code change.

---

These instructions are authoritative for Copilot and automation that consumes repository-local guidance. Keep this file concise and update it when repo policies change.
