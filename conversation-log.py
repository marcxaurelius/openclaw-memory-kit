#!/usr/bin/env python3
"""
Conversation Logger - Session History to JSONL Extractor

Two modes:
  --direct   Reads directly from OpenClaw session files on disk (no agent needed)
  --input    Reads from a JSON file (piped from sessions_history)

Usage:
    conversation-log.py --direct [--session-key KEY] [--openclaw-dir DIR]
    conversation-log.py --input <file> --session-key <key>
    conversation-log.py --help
"""

import json
import sys
import argparse
import gzip
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

# Truncation detection threshold: flag content over this length as potentially truncated
TRUNCATION_LENGTH_THRESHOLD = 15000  # chars — sessions_history truncates around this


class ConversationLogger:
    def __init__(self, workspace_root: Path):
        self.workspace = workspace_root
        self.logs_dir = workspace_root / "logs" / "sessions"
        self.watermark_file = self.logs_dir / ".watermark"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def read_watermark(self, session_key: str) -> Optional[str]:
        """Read last processed timestamp for a session."""
        if not self.watermark_file.exists():
            return None
        try:
            with open(self.watermark_file, 'r') as f:
                watermarks = json.load(f)
                return watermarks.get(session_key)
        except (json.JSONDecodeError, FileNotFoundError):
            return None

    def write_watermark(self, session_key: str, timestamp: str):
        """Update watermark for a session."""
        watermarks = {}
        if self.watermark_file.exists():
            try:
                with open(self.watermark_file, 'r') as f:
                    watermarks = json.load(f)
            except json.JSONDecodeError:
                pass
        watermarks[session_key] = timestamp
        with open(self.watermark_file, 'w') as f:
            json.dump(watermarks, f, indent=2)

    def parse_timestamp(self, ts: Any) -> Optional[datetime]:
        """Parse timestamp from various formats (epoch ms, epoch s, ISO string)."""
        if not ts:
            return None
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts / 1000 if ts > 1e12 else ts)
        if isinstance(ts, str):
            ts_normalized = ts.replace('+00:00', 'Z').replace('Z', '')
            if '.' in ts_normalized:
                ts_normalized = ts_normalized.split('.')[0]
            try:
                return datetime.strptime(ts_normalized, "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                pass
        return None

    def ts_to_comparable(self, ts: Any) -> float:
        """Convert any timestamp to epoch seconds for comparison."""
        if isinstance(ts, (int, float)):
            return ts / 1000 if ts > 1e12 else ts
        dt = self.parse_timestamp(ts)
        return dt.timestamp() if dt else 0

    def is_content_truncated(self, text: str) -> bool:
        """Check if content appears truncated (marker-based + length-based)."""
        if not text:
            return False
        # Marker-based detection
        truncation_markers = ['...', '[truncated]', '(truncated)', '…(truncated)…']
        if any(marker in text[-100:] for marker in truncation_markers):
            return True
        # Length-based detection — flag suspiciously long content that may have been cut
        if len(text) >= TRUNCATION_LENGTH_THRESHOLD:
            return True
        return False

    def extract_file_paths(self, tool_call: Dict) -> List[str]:
        """Extract file paths from tool calls for re-read attempts."""
        paths = []
        tool = tool_call.get('tool', '')
        inp = tool_call.get('input', {})
        if tool in ('read', 'Read') and isinstance(inp, dict):
            path = inp.get('file_path') or inp.get('path')
            if path:
                paths.append(path)
        return paths

    def reread_file(self, path: str) -> Optional[str]:
        """Attempt to re-read a file if it exists on disk."""
        try:
            file_path = self.workspace / path if not Path(path).is_absolute() else Path(path)
            if file_path.exists() and file_path.is_file():
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
        except Exception as e:
            print(f"Warning: Could not re-read {path}: {e}", file=sys.stderr)
        return None

    def extract_content_text(self, content: Any) -> str:
        """Extract plain text from content (string or content block array)."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(block.get('text', '') or block.get('thinking', ''))
                elif isinstance(block, str):
                    parts.append(block)
            return '\n'.join(p for p in parts if p)
        return str(content) if content else ''

    def process_message_entry(self, msg: Dict, seq: int) -> Dict:
        """Process a message dict into our JSONL record format."""
        role = msg.get('role', 'unknown')
        content = msg.get('content', '')

        # Extract thinking blocks as separate role
        thinking_text = ''
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get('type') == 'thinking':
                    thinking_text = block.get('thinking', '')

        content_text = self.extract_content_text(content)

        record = {
            "seq": seq,
            "ts": msg.get('timestamp', ''),
            "role": role,
            "content": content_text,
            "tool_calls": [],
            "model": msg.get('model', ''),
            "truncated": False,
            "truncated_fields": []
        }

        # If there's a thinking block, store it
        if thinking_text:
            record["thinking"] = thinking_text

        # Check for truncated content
        if self.is_content_truncated(content_text):
            record['truncated'] = True
            record['truncated_fields'].append('content')

        # Process tool calls from content blocks
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get('type') == 'toolCall':
                    tool_record = {
                        "tool": block.get('name', ''),
                        "input": block.get('arguments', {}),
                        "output": ""
                    }
                    record['tool_calls'].append(tool_record)

        # Process explicit tool_calls field
        if 'tool_calls' in msg:
            for tc in msg['tool_calls']:
                tool_record = {
                    "tool": tc.get('tool', tc.get('name', '')),
                    "input": tc.get('input', tc.get('arguments', {})),
                    "output": tc.get('output', tc.get('result', ''))
                }
                output_str = str(tool_record['output'])
                if self.is_content_truncated(output_str):
                    record['truncated'] = True
                    record['truncated_fields'].append(f"tool_call.{tool_record['tool']}")
                    file_paths = self.extract_file_paths(tool_record)
                    for path in file_paths:
                        full_content = self.reread_file(path)
                        if full_content:
                            tool_record['output'] = full_content
                            tool_record['reread'] = True
                            print(f"Re-read {path} successfully", file=sys.stderr)
                record['tool_calls'].append(tool_record)

        return record

    def get_output_file(self, ts: Any = None) -> Path:
        """Get output JSONL file path (one per day)."""
        if ts:
            dt = self.parse_timestamp(ts)
            if dt:
                return self.logs_dir / f"{dt.strftime('%Y-%m-%d')}.jsonl"
        today = datetime.now().strftime("%Y-%m-%d")
        return self.logs_dir / f"{today}.jsonl"

    def process_history(self, history_data: List[Dict], session_key: str, backfill: bool = False):
        """Process history entries from sessions_history JSON and write to JSONL."""
        watermark = None if backfill else self.read_watermark(session_key)
        watermark_epoch = self.ts_to_comparable(watermark) if watermark else 0

        new_entries = []
        latest_epoch = watermark_epoch

        for entry in history_data:
            entry_ts = entry.get('timestamp', entry.get('ts'))
            entry_epoch = self.ts_to_comparable(entry_ts)

            if entry_epoch <= watermark_epoch:
                continue

            new_entries.append(entry)
            if entry_epoch > latest_epoch:
                latest_epoch = entry_epoch

        if not new_entries:
            print(f"No new entries to process (watermark: {watermark})", file=sys.stderr)
            return

        output_file = self.get_output_file()
        with open(output_file, 'a') as f:
            for idx, entry in enumerate(new_entries, start=1):
                record = self.process_message_entry(entry, idx)
                f.write(json.dumps(record) + '\n')

        # Store watermark as epoch ms for consistency
        watermark_val = int(latest_epoch * 1000) if latest_epoch < 1e12 else int(latest_epoch)
        self.write_watermark(session_key, watermark_val)
        print(f"Processed {len(new_entries)} new entries to {output_file}", file=sys.stderr)
        self.cleanup()

    def process_direct(self, openclaw_dir: Path, session_key: Optional[str] = None, backfill: bool = False):
        """Read directly from OpenClaw session JSONL files on disk."""
        sessions_dir = openclaw_dir / "agents" / "main" / "sessions"
        sessions_index = sessions_dir / "sessions.json"

        if not sessions_index.exists():
            print(f"Error: sessions.json not found at {sessions_index}", file=sys.stderr)
            sys.exit(1)

        with open(sessions_index) as f:
            sessions_map = json.load(f)

        # Determine which sessions to process
        if session_key:
            if session_key not in sessions_map:
                print(f"Error: session key '{session_key}' not found in sessions.json", file=sys.stderr)
                sys.exit(1)
            targets = {session_key: sessions_map[session_key]}
        else:
            targets = sessions_map

        total_processed = 0
        for skey, sdata in targets.items():
            session_id = sdata.get('sessionId')
            if not session_id:
                continue

            session_file = sessions_dir / f"{session_id}.jsonl"
            if not session_file.exists():
                print(f"Warning: session file missing for {skey}: {session_file}", file=sys.stderr)
                continue

            count = self._process_session_file(session_file, skey, backfill)
            total_processed += count

        if total_processed > 0:
            print(f"Total: {total_processed} new entries processed", file=sys.stderr)
            self.cleanup()
        else:
            print("No new entries to process", file=sys.stderr)

    def _process_session_file(self, session_file: Path, session_key: str, backfill: bool) -> int:
        """Process a single OpenClaw session JSONL file."""
        watermark = None if backfill else self.read_watermark(session_key)
        watermark_epoch = self.ts_to_comparable(watermark) if watermark else 0

        new_entries = []
        latest_epoch = watermark_epoch

        with open(session_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Only process message entries
                if entry.get('type') != 'message':
                    continue

                msg = entry.get('message', {})
                ts_str = entry.get('timestamp', '')
                entry_epoch = self.ts_to_comparable(ts_str)

                if entry_epoch <= watermark_epoch:
                    continue

                # Add timestamp to message for processing
                msg['timestamp'] = ts_str
                new_entries.append(msg)
                if entry_epoch > latest_epoch:
                    latest_epoch = entry_epoch

        if not new_entries:
            return 0

        # Write entries, grouped by date
        entries_by_date = {}
        for msg in new_entries:
            dt = self.parse_timestamp(msg.get('timestamp', ''))
            date_key = dt.strftime('%Y-%m-%d') if dt else datetime.now().strftime('%Y-%m-%d')
            entries_by_date.setdefault(date_key, []).append(msg)

        total = 0
        for date_key, entries in entries_by_date.items():
            output_file = self.logs_dir / f"{date_key}.jsonl"
            with open(output_file, 'a') as f:
                for idx, msg in enumerate(entries, start=1):
                    record = self.process_message_entry(msg, idx)
                    f.write(json.dumps(record) + '\n')
            total += len(entries)
            print(f"  {session_key}: {len(entries)} entries -> {output_file.name}", file=sys.stderr)

        # Update watermark
        watermark_val = int(latest_epoch * 1000) if latest_epoch < 1e12 else int(latest_epoch)
        self.write_watermark(session_key, watermark_val)
        return total

    def cleanup(self):
        """Gzip files older than 7 days, delete files older than 30 days."""
        now = datetime.now()

        for file in self.logs_dir.glob("*.jsonl"):
            try:
                file_date = datetime.strptime(file.stem, "%Y-%m-%d")
                age_days = (now - file_date).days
                if age_days > 30:
                    file.unlink()
                    print(f"Deleted old file: {file.name}", file=sys.stderr)
                elif age_days > 7:
                    gz_path = file.with_suffix('.jsonl.gz')
                    if not gz_path.exists():
                        with open(file, 'rb') as f_in:
                            with gzip.open(gz_path, 'wb') as f_out:
                                shutil.copyfileobj(f_in, f_out)
                        file.unlink()
                        print(f"Compressed: {file.name} -> {gz_path.name}", file=sys.stderr)
            except (ValueError, OSError):
                pass

        for file in self.logs_dir.glob("*.jsonl.gz"):
            try:
                file_date = datetime.strptime(file.stem.replace('.jsonl', ''), "%Y-%m-%d")
                if (now - file_date).days > 30:
                    file.unlink()
                    print(f"Deleted old compressed file: {file.name}", file=sys.stderr)
            except (ValueError, OSError):
                pass


def main():
    parser = argparse.ArgumentParser(
        description="Conversation Logger - Extract session history to JSONL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('--direct', action='store_true',
                        help='Read directly from OpenClaw session files on disk (no agent needed)')
    parser.add_argument('--input', help='Input JSON file with session history (alternative to --direct)')
    parser.add_argument('--session-key', help='Session key (required for --input, optional filter for --direct)')
    parser.add_argument('--backfill', action='store_true', help='Process all history, ignore watermark')
    parser.add_argument('--workspace', default='/home/steve/.openclaw/workspace', help='Workspace root path')
    parser.add_argument('--openclaw-dir', default='/home/steve/.openclaw', help='OpenClaw root directory')

    args = parser.parse_args()

    if not args.direct and not args.input:
        parser.error("Either --direct or --input is required")

    logger = ConversationLogger(Path(args.workspace))

    if args.direct:
        logger.process_direct(Path(args.openclaw_dir), args.session_key, args.backfill)
    else:
        if not args.session_key:
            parser.error("--session-key is required when using --input")
        try:
            with open(args.input, 'r') as f:
                history = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error reading input file: {e}", file=sys.stderr)
            sys.exit(1)

        if isinstance(history, dict):
            if 'messages' in history:
                history = history['messages']
            elif 'history' in history:
                history = history['history']
        if not isinstance(history, list):
            print("Error: Input must be a JSON array or object with 'messages'/'history' key", file=sys.stderr)
            sys.exit(1)

        logger.process_history(history, args.session_key, args.backfill)


if __name__ == '__main__':
    main()
