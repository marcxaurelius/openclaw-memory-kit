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
| `memory-index.py` | Generates compact categorized index of MEMORY.md (~70% token reduction) |

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
- On compaction detection: run `python3 scripts/post-compaction-inject.py --workspace . --log-event` — do NOT ask user, do NOT re-request context.
- Hourly summaries at `memory/hourly/YYYY-MM-DD.md` feed the daily notes pipeline.

### Action Log
- BEFORE any external action (Discord, DM, email, API call, file creation): check `logs/actions.jsonl` for duplicates within 2 hours.
- Check: `python3 scripts/action-log.py check --type TYPE --target TARGET --summary "..." --window 2`
- If duplicate found (exit code 1): confirm with user before repeating.
- Log BEFORE executing: `python3 scripts/action-log.py log --type TYPE --target TARGET --summary "..."`
- Update status after: `python3 scripts/action-log.py update --ts TS --status success|failed`
- Heartbeat tasks MUST check action log — heartbeat re-fires are the primary duplicate source.

### MEMORY.md Loading
- On session start, run `memory_search` for active context rather than relying solely on full MEMORY.md injection.
- Compact index at `memory/memory-index.md` provides categorized quick-scan (~500 tokens vs ~1900).
- Regenerate index when MEMORY.md changes: `python3 scripts/memory-index.py --workspace .`
- Index is auto-generated — never edit manually.
```

### 5. Add to HEARTBEAT.md

```markdown
### Session Resilience (Hourly)
- Conversation logger: `sessions_history` → `/tmp/history.json` → `python3 scripts/conversation-log.py --input /tmp/history.json --session-key KEY`
- Hourly summarizer: `python3 scripts/hourly-summarizer.py --workspace .`
- Memory index: If MEMORY.md changed, regenerate: `python3 scripts/memory-index.py --workspace .`

### Retention Cleanup (Daily)
- memory/hourly/ — Archive files >7 days old
- logs/sessions/ — Gzip files >7 days, delete >30 days
- logs/actions.jsonl — Trim entries >30 days old
```

### 6. Generate initial index

```bash
python3 scripts/memory-index.py --workspace /path/to/your/workspace
```

### 7. Optional: Backfill conversation history

```bash
# Agent calls sessions_history, writes to temp file, then:
python3 scripts/conversation-log.py --input /tmp/history.json --session-key YOUR_SESSION_KEY --backfill
```

## How It Works

```
SESSION START
  Load memory-index.md (compact) instead of full MEMORY.md
  Load today's hourly summaries

DURING SESSION
  Conversation logger captures all turns to JSONL (hourly cron)
  Action log prevents duplicate external actions
  Hourly summarizer distills JSONL → structured markdown

COMPACTION DETECTED
  Post-compaction injector assembles recovery payload (~8K tokens)
  Sources: hourly summaries, recent messages, thinking blocks, MEMORY.md
  Agent continues seamlessly
```

## License

MIT
