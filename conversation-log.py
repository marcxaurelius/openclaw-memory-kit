#!/usr/bin/env python3
"""
Conversation Logger - Session History to JSONL Extractor

Reads session history JSON and writes to append-only JSONL files.
Maintains watermark to avoid duplicates. Handles retention and cleanup.

Usage:
    conversation-log.py --input <file> --session-key <key> [--backfill]
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
        """Parse timestamp from various formats."""
        if not ts:
            return None
        
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts / 1000 if ts > 1e12 else ts)
        
        if isinstance(ts, str):
            # Normalize timezone indicators
            ts_normalized = ts.replace('+00:00', 'Z').replace('Z', '')
            
            # Remove fractional seconds if present
            if '.' in ts_normalized:
                ts_normalized = ts_normalized.split('.')[0]
            
            # Try ISO format without timezone
            try:
                return datetime.strptime(ts_normalized, "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                pass
        
        return None
    
    def is_content_truncated(self, text: str) -> bool:
        """Check if content appears truncated."""
        if not text:
            return False
        truncation_markers = ['...', '[truncated]', '(truncated)', '…']
        return any(marker in text[-50:] for marker in truncation_markers)
    
    def extract_file_paths(self, tool_call: Dict) -> List[str]:
        """Extract file paths from tool calls."""
        paths = []
        
        if tool_call.get('tool') in ['read', 'Read']:
            inp = tool_call.get('input', {})
            if isinstance(inp, dict):
                path = inp.get('file_path') or inp.get('path')
                if path:
                    paths.append(path)
        
        return paths
    
    def reread_file(self, path: str) -> Optional[str]:
        """Attempt to re-read a file if it exists."""
        try:
            file_path = self.workspace / path if not Path(path).is_absolute() else Path(path)
            if file_path.exists() and file_path.is_file():
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
        except Exception as e:
            print(f"Warning: Could not re-read {path}: {e}", file=sys.stderr)
        return None
    
    def process_entry(self, entry: Dict, seq: int) -> Dict:
        """Process a single history entry into JSONL record."""
        record = {
            "seq": seq,
            "ts": entry.get('timestamp', entry.get('ts', '')),
            "role": entry.get('role', 'unknown'),
            "content": entry.get('content', ''),
            "tool_calls": [],
            "model": entry.get('model', ''),
            "truncated": False,
            "truncated_fields": []
        }
        
        # Check for truncated content
        if self.is_content_truncated(record['content']):
            record['truncated'] = True
            record['truncated_fields'].append('content')
        
        # Process tool calls
        if 'tool_calls' in entry:
            for tc in entry['tool_calls']:
                tool_record = {
                    "tool": tc.get('tool', tc.get('name', '')),
                    "input": tc.get('input', tc.get('arguments', {})),
                    "output": tc.get('output', tc.get('result', ''))
                }
                
                # Check if output is truncated
                output_str = str(tool_record['output'])
                if self.is_content_truncated(output_str):
                    record['truncated'] = True
                    record['truncated_fields'].append(f"tool_call.{tool_record['tool']}")
                    
                    # Try to re-read if it's a file read operation
                    file_paths = self.extract_file_paths(tool_record)
                    if file_paths:
                        for path in file_paths:
                            full_content = self.reread_file(path)
                            if full_content:
                                tool_record['output'] = full_content
                                tool_record['reread'] = True
                                print(f"Re-read {path} successfully", file=sys.stderr)
                
                record['tool_calls'].append(tool_record)
        
        return record
    
    def get_output_file(self) -> Path:
        """Get output JSONL file path (one per day)."""
        today = datetime.now().strftime("%Y-%m-%d")
        return self.logs_dir / f"{today}.jsonl"
    
    def process_history(self, history_data: List[Dict], session_key: str, backfill: bool = False):
        """Process history entries and write to JSONL."""
        watermark = None if backfill else self.read_watermark(session_key)
        watermark_dt = self.parse_timestamp(watermark) if watermark else None
        
        new_entries = []
        latest_ts = watermark
        
        for entry in history_data:
            entry_ts = entry.get('timestamp', entry.get('ts'))
            entry_dt = self.parse_timestamp(entry_ts)
            
            # Skip if before watermark
            if watermark_dt and entry_dt and entry_dt <= watermark_dt:
                continue
            
            new_entries.append(entry)
            
            # Track latest timestamp
            if entry_dt and (not latest_ts or entry_dt > self.parse_timestamp(latest_ts)):
                latest_ts = entry_ts
        
        if not new_entries:
            print(f"No new entries to process (watermark: {watermark})", file=sys.stderr)
            return
        
        # Write to JSONL
        output_file = self.get_output_file()
        with open(output_file, 'a') as f:
            for idx, entry in enumerate(new_entries, start=1):
                record = self.process_entry(entry, idx)
                f.write(json.dumps(record) + '\n')
        
        print(f"Processed {len(new_entries)} new entries to {output_file}", file=sys.stderr)
        
        # Update watermark
        if latest_ts:
            self.write_watermark(session_key, latest_ts)
            print(f"Updated watermark to {latest_ts}", file=sys.stderr)
        
        # Run cleanup
        self.cleanup()
    
    def cleanup(self):
        """Gzip files older than 7 days, delete files older than 30 days."""
        now = datetime.now()
        
        for file in self.logs_dir.glob("*.jsonl"):
            # Parse date from filename
            try:
                file_date_str = file.stem  # YYYY-MM-DD
                file_date = datetime.strptime(file_date_str, "%Y-%m-%d")
                age_days = (now - file_date).days
                
                # Delete if older than 30 days
                if age_days > 30:
                    file.unlink()
                    print(f"Deleted old file: {file.name}", file=sys.stderr)
                # Gzip if older than 7 days
                elif age_days > 7:
                    gz_path = file.with_suffix('.jsonl.gz')
                    if not gz_path.exists():
                        with open(file, 'rb') as f_in:
                            with gzip.open(gz_path, 'wb') as f_out:
                                shutil.copyfileobj(f_in, f_out)
                        file.unlink()
                        print(f"Compressed: {file.name} -> {gz_path.name}", file=sys.stderr)
            except (ValueError, OSError) as e:
                print(f"Warning: Could not process {file.name}: {e}", file=sys.stderr)
        
        # Also delete .gz files older than 30 days
        for file in self.logs_dir.glob("*.jsonl.gz"):
            try:
                file_date_str = file.stem.replace('.jsonl', '')
                file_date = datetime.strptime(file_date_str, "%Y-%m-%d")
                age_days = (now - file_date).days
                
                if age_days > 30:
                    file.unlink()
                    print(f"Deleted old compressed file: {file.name}", file=sys.stderr)
            except (ValueError, OSError) as e:
                print(f"Warning: Could not process {file.name}: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Conversation Logger - Extract session history to JSONL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('--input', required=True, help='Input JSON file with session history')
    parser.add_argument('--session-key', required=True, help='Session key identifier')
    parser.add_argument('--backfill', action='store_true', help='Process all history, ignore watermark')
    parser.add_argument('--workspace', default='/home/steve/.openclaw/workspace', help='Workspace root path')
    
    args = parser.parse_args()
    
    # Read input history
    try:
        with open(args.input, 'r') as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error reading input file: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Ensure it's a list
    if isinstance(history, dict):
        if 'messages' in history:
            history = history['messages']
        elif 'history' in history:
            history = history['history']
    if not isinstance(history, list):
        print("Error: Input must be a JSON array or object with 'messages'/'history' key", file=sys.stderr)
        sys.exit(1)
    
    # Process
    logger = ConversationLogger(Path(args.workspace))
    logger.process_history(history, args.session_key, args.backfill)


if __name__ == '__main__':
    main()
