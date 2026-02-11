# OpenClaw Memory Kit

Session resilience and memory architecture scripts for OpenClaw agents. Survives context compaction with zero noticeable knowledge loss.

**Requirements:** Python 3 standard library only. No pip installs.

## Scripts

| Script | Purpose |
|--------|---------|
| `conversation-log.py` | Extracts session history to append-only JSONL (ground truth logging) |
| `action-log.py` | Prevents duplicate external actions (Discord posts, emails, etc.) |
| `hourly-summarizer.py` | Structured hourly summaries from JSONL logs |
| `post-compaction-inject.py` | Assembles ~8K token recovery payload after compaction |

## Installation

### 1. Copy scripts to your workspace

```bash
# Clone this repo
git clone https://github.com/marcxaurelius/openclaw-memory-kit.git /tmp/openclaw-memory-kit

# Copy scripts to your workspace
cp /tmp/openclaw-memory-kit/*.py /path/to/your/workspace/scripts/
chmod +x /path/to/your/workspace/scripts/*.py
```

### 2. Create directories

```bash
cd /path/to/your/workspace
mkdir -p logs/sessions memory/hourly
touch logs/sessions/.gitkeep memory/hourly/.gitkeep
```

### 3. Add to `.gitignore`

```
logs/sessions/*.jsonl
logs/sessions/*.gz
logs/sessions/.watermark
logs/actions.jsonl
memory/hourly/.summarizer-state
```

### 4. Add to AGENTS.md

Add these sections inside your Memory System section:

```markdown
### Session Layer

- Session context is ephemeral. It WILL compact. Treat it as unreliable for long-running work.
- Ground truth: append-only JSONL at `logs/sessions/YYYY-MM-DD.jsonl` — all turns, all roles, all thinking blocks.
- Lifecycle: start → accumulate → flush before compaction → compaction → auto-recover → continue.
- Hourly summaries at `memory/hourly/YYYY-MM-DD.md` feed the daily notes pipeline.

### Post-Compaction Recovery (MANDATORY)

On ANY compaction signature (summary block, context reset, pre-compaction flush prompt):

1. **Immediately** pull `sessions_history` (limit=50) for the current session. This is step one, before anything else.
2. **Do NOT trust the platform compaction summary as complete.** It omits recent messages.
3. **Do NOT skip this because context "looks sufficient."** No judgment call. No "looks good enough."
4. Write any unrecorded conversation to `memory/YYYY-MM-DD.md` from the sessions_history pull.
5. Run `python3 scripts/post-compaction-inject.py --workspace . --log-event` for structured recovery.
6. Do NOT ask user, do NOT re-request context.

**This is unconditional. Every compaction, every time. Zero exceptions.**

`sessions_history` is the authoritative recovery source — it's compaction-proof and has everything. Hourly summaries are supplementary for structured context. Without sessions_history, you revert to stale knowledge and embarrass yourself.

### Pre-Compaction Flush

When you receive a pre-compaction flush prompt:
1. Pull `sessions_history` (limit=30) and capture any conversation not yet in daily notes.
2. Write to `memory/YYYY-MM-DD.md` — actual content, not generic status updates.
3. The flush is your last chance before context wipes. Treat it accordingly.

### Action Log

- BEFORE any external action (Discord, DM, email, API call, file creation): check `logs/actions.jsonl` for duplicates within 2 hours.
- Check: `python3 scripts/action-log.py check --type TYPE --target TARGET --summary "..." --window 2`
- If duplicate found (exit code 1): confirm with user before repeating.
- Log BEFORE executing: `python3 scripts/action-log.py log --type TYPE --target TARGET --summary "..."`
- Update status after: `python3 scripts/action-log.py update --ts TS --status success|failed`
- Heartbeat tasks MUST check action log — heartbeat re-fires are the primary duplicate source.
```

### 5. Add to HEARTBEAT.md

```markdown
### Session Resilience (Hourly)
- Conversation logger: `sessions_history` → `/tmp/history.json` → `python3 scripts/conversation-log.py --input /tmp/history.json --session-key KEY`
- Hourly summarizer: `python3 scripts/hourly-summarizer.py --workspace .`

### Retention Cleanup (Daily)
- memory/hourly/ — Archive files >7 days old
- logs/sessions/ — Gzip files >7 days, delete >30 days
- logs/actions.jsonl — Trim entries >30 days old
```

### 6. Optional: Backfill conversation history

```bash
# Agent calls sessions_history, writes to temp file, then:
python3 scripts/conversation-log.py --input /tmp/history.json --session-key YOUR_SESSION_KEY --backfill
```

### 7. Optional: Direct disk access mode

If your agent runs on the same machine as OpenClaw, skip the API and read session files directly:

```bash
# Reads from ~/.openclaw/agents/main/sessions/*.jsonl
python3 scripts/conversation-log.py --direct --openclaw-dir ~/.openclaw
```

## How It Works

```
SESSION START
  Load today + yesterday daily notes
  Run memory_search for active context

DURING SESSION
  Conversation logger captures all turns to JSONL (hourly cron)
  Action log prevents duplicate external actions
  Hourly summarizer distills JSONL → structured markdown

PRE-COMPACTION FLUSH
  Pull sessions_history (limit=30)
  Write unrecorded conversation to daily notes
  This is your last chance — capture actual content, not status updates

COMPACTION DETECTED
  IMMEDIATELY pull sessions_history (limit=50) — step one, no exceptions
  Do NOT trust the compaction summary as complete
  Write unrecorded conversation to daily notes
  Run post-compaction-inject.py for structured recovery
  Agent continues seamlessly

KEY INSIGHT:
  sessions_history is compaction-proof (platform-level log).
  It has everything. Hourly summaries are supplementary.
  The compaction summary WILL omit recent messages.
  Trust sessions_history, not the summary.
```

## Why This Matters

Context compaction is inevitable in long-running agent sessions. Without this kit:
- The agent loses recent conversation context
- It reverts to stale knowledge and makes embarrassing mistakes
- It trusts the platform's compaction summary, which omits recent messages
- Recovery depends on the user re-explaining what just happened

With this kit:
- `sessions_history` provides compaction-proof ground truth
- The recovery protocol is unconditional — no judgment calls, no "looks sufficient"
- Hourly summaries provide structured context between compactions
- The agent recovers seamlessly without user intervention

## License

MIT
