#!/usr/bin/env python3
"""
Hourly Summarizer - Converts conversation JSONL logs to structured hourly summaries.

Reads logs from logs/sessions/*.jsonl and produces markdown summaries in memory/hourly/.
Uses rule-based extraction to identify decisions, actions, tasks, and context.
"""

import argparse
import json
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


class HourlySummarizer:
    """Extract structured summaries from conversation logs."""

    # Pattern matchers for rule-based extraction
    DECISION_PATTERNS = [
        r'\b(?:chose|choosing|decided to|decided on|going with|will use|opted for)\b',
        r'\b(?:rejected|not using|avoiding|won\'t use|skipping)\b',
        r'\breason:\s*(.+)',
    ]

    ACTION_PATTERNS = [
        r'\b(?:created|built|wrote|posted|sent|deployed|updated|modified|deleted)\b',
        r'\b(?:running|executing|spawning|starting|stopping)\b',
    ]

    TASK_PATTERNS = [
        r'Phase\s+\d+',
        r'\b(?:working on|building|debugging|implementing|designing|testing)\b',
        r'\bin progress\b',
    ]

    CONTEXT_PATTERNS = [
        r'\buser (?:said|mentioned|requested|asked|wants|needs)\b',
        r'\bconstraint:\s*(.+)',
        r'\brequirement:\s*(.+)',
        r'\bexplicitly\s+(?:rejected|requested|specified)\b',
    ]

    def __init__(self, workspace_path: str):
        self.workspace = Path(workspace_path)
        self.logs_dir = self.workspace / "logs" / "sessions"
        self.output_dir = self.workspace / "memory" / "hourly"
        self.state_file = self.output_dir / ".summarizer-state"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def load_state(self) -> Dict[str, int]:
        """Load the last processed timestamp per session key."""
        if not self.state_file.exists():
            return {}
        try:
            with open(self.state_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def save_state(self, state: Dict[str, int]):
        """Save the last processed timestamp per session key."""
        with open(self.state_file, 'w') as f:
            json.dump(state, f, indent=2)

    def extract_text(self, content) -> str:
        """Extract text from content field (handles both string and array formats)."""
        if isinstance(content, str):
            return content
        elif isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict):
                    if 'text' in block:
                        texts.append(block['text'])
                    elif 'thinking' in block:
                        texts.append(block['thinking'])
            return ' '.join(texts)
        return ''

    def matches_patterns(self, text: str, patterns: List[str]) -> bool:
        """Check if text matches any of the given regex patterns."""
        text_lower = text.lower()
        return any(re.search(pattern, text_lower, re.IGNORECASE) for pattern in patterns)

    def extract_decisions(self, entry: dict) -> List[str]:
        """Extract decision statements from an entry."""
        decisions = []
        text = self.extract_text(entry.get('content', ''))
        
        if not text or not self.matches_patterns(text, self.DECISION_PATTERNS):
            return decisions

        # Split into sentences and look for decision patterns
        sentences = re.split(r'[.!?]\s+', text)
        for sentence in sentences:
            if self.matches_patterns(sentence, self.DECISION_PATTERNS):
                # Clean up and truncate if needed
                clean = sentence.strip()
                if clean and len(clean) > 10:  # Skip very short matches
                    if len(clean) > 150:
                        clean = clean[:147] + "..."
                    decisions.append(clean)

        return decisions

    def extract_actions(self, entry: dict) -> List[str]:
        """Extract actions from tool calls and content."""
        actions = []
        
        # Extract from tool calls
        tool_calls = entry.get('tool_calls', [])
        if tool_calls:
            for call in tool_calls:
                tool = call.get('tool', '')
                input_data = call.get('input', '')
                
                if tool == 'exec':
                    cmd = input_data.get('command', '') if isinstance(input_data, dict) else str(input_data)
                    if cmd:
                        actions.append(f"Executed: {cmd[:100]}")
                
                elif tool == 'message':
                    target = input_data.get('target', '') if isinstance(input_data, dict) else ''
                    if target:
                        actions.append(f"Posted to {target}")
                
                elif tool == 'sessions_spawn':
                    label = input_data.get('label', '') if isinstance(input_data, dict) else ''
                    if label:
                        actions.append(f"Spawned subagent: {label}")
                
                elif tool in ['Write', 'write']:
                    path = input_data.get('file_path', '') or input_data.get('path', '')
                    if isinstance(input_data, dict) and path:
                        filename = Path(path).name
                        actions.append(f"Created/updated {filename}")

        # Extract from content
        text = self.extract_text(entry.get('content', ''))
        if text and self.matches_patterns(text, self.ACTION_PATTERNS):
            sentences = re.split(r'[.!?]\s+', text)
            for sentence in sentences:
                if self.matches_patterns(sentence, self.ACTION_PATTERNS):
                    clean = sentence.strip()
                    if clean and len(clean) > 10 and len(clean) < 150:
                        # Avoid duplicating tool call extractions
                        if not any(action in clean for action in actions):
                            actions.append(clean)

        return actions[:10]  # Limit to avoid overwhelming output

    def extract_tasks(self, entry: dict) -> List[str]:
        """Extract task/project state from content."""
        tasks = []
        text = self.extract_text(entry.get('content', ''))
        
        if not text or not self.matches_patterns(text, self.TASK_PATTERNS):
            return tasks

        sentences = re.split(r'[.!?]\s+', text)
        for sentence in sentences:
            if self.matches_patterns(sentence, self.TASK_PATTERNS):
                clean = sentence.strip()
                if clean and len(clean) > 10:
                    if len(clean) > 150:
                        clean = clean[:147] + "..."
                    tasks.append(clean)

        return tasks

    def extract_context(self, entry: dict) -> List[str]:
        """Extract key context (constraints, requirements, user preferences)."""
        context = []
        text = self.extract_text(entry.get('content', ''))
        
        if not text or not self.matches_patterns(text, self.CONTEXT_PATTERNS):
            return context

        sentences = re.split(r'[.!?]\s+', text)
        for sentence in sentences:
            if self.matches_patterns(sentence, self.CONTEXT_PATTERNS):
                clean = sentence.strip()
                if clean and len(clean) > 10:
                    if len(clean) > 150:
                        clean = clean[:147] + "..."
                    context.append(clean)

        return context

    def is_meaningful_hour(self, hour_data: dict) -> bool:
        """Check if an hour has meaningful content worth summarizing."""
        if not hour_data:
            return False
        
        # Count meaningful content
        total_items = (
            len(hour_data.get('decisions', [])) +
            len(hour_data.get('actions', [])) +
            len(hour_data.get('tasks', [])) +
            len(hour_data.get('context', []))
        )
        
        return total_items > 0

    def group_by_hour(self, entries: List[dict], last_ts: int) -> Dict[str, dict]:
        """Group log entries by hour and extract summaries."""
        hourly_data = defaultdict(lambda: {
            'decisions': [],
            'actions': [],
            'tasks': [],
            'context': [],
        })
        
        for entry in entries:
            ts = entry.get('ts', 0)
            
            # Skip entries we've already processed
            if ts <= last_ts:
                continue
            
            # Convert timestamp to hour key
            dt = datetime.fromtimestamp(ts / 1000)
            hour_key = dt.strftime('%Y-%m-%d %H:00')
            
            # Extract based on role
            role = entry.get('role', '')
            
            # Thinking blocks are gold for decisions
            if role == 'thinking':
                decisions = self.extract_decisions(entry)
                hourly_data[hour_key]['decisions'].extend(decisions)
            
            # Assistant messages for actions and tasks
            elif role == 'assistant':
                actions = self.extract_actions(entry)
                tasks = self.extract_tasks(entry)
                hourly_data[hour_key]['actions'].extend(actions)
                hourly_data[hour_key]['tasks'].extend(tasks)
            
            # User messages for context
            elif role == 'user':
                context = self.extract_context(entry)
                hourly_data[hour_key]['context'].extend(context)

        # Deduplicate within each hour
        for hour_key in hourly_data:
            for category in ['decisions', 'actions', 'tasks', 'context']:
                hourly_data[hour_key][category] = list(dict.fromkeys(hourly_data[hour_key][category]))

        return dict(hourly_data)

    def format_hour_summary(self, hour_key: str, data: dict) -> str:
        """Format an hour's data into markdown."""
        # Parse hour range
        dt = datetime.strptime(hour_key, '%Y-%m-%d %H:00')
        hour_start = dt.strftime('%H:%M')
        hour_end = f"{dt.hour:02d}:59"
        
        lines = [f"### {hour_start} — {hour_end}\n"]
        
        if data.get('decisions'):
            lines.append("**Decisions:**")
            for decision in data['decisions']:
                lines.append(f"- {decision}")
            lines.append("")
        
        if data.get('actions'):
            lines.append("**Actions Taken:**")
            for action in data['actions']:
                lines.append(f"- {action}")
            lines.append("")
        
        if data.get('tasks'):
            lines.append("**Active Tasks:**")
            for task in data['tasks']:
                lines.append(f"- {task}")
            lines.append("")
        
        if data.get('context'):
            lines.append("**Key Context:**")
            for ctx in data['context']:
                lines.append(f"- {ctx}")
            lines.append("")
        
        return '\n'.join(lines)

    def get_existing_hours(self, output_file: Path) -> Set[str]:
        """Get set of hours already in the output file to avoid duplicates."""
        existing = set()
        if not output_file.exists():
            return existing
        
        with open(output_file, 'r') as f:
            for line in f:
                # Match hour headers like "### 14:00 — 14:59"
                match = re.match(r'###\s+(\d{2}):00\s+—\s+\d{2}:59', line)
                if match:
                    hour = match.group(1)
                    # Construct hour key from file date
                    date = output_file.stem  # YYYY-MM-DD
                    existing.add(f"{date} {hour}:00")
        
        return existing

    def process_session(self, session_key: str, state: Dict[str, int]) -> Tuple[int, int]:
        """Process a single session's logs. Returns (entries_processed, hours_written)."""
        # Find log file(s) for this session
        # Session keys might be in format: agent:main:discord:channel:ID
        # Log files are: YYYY-MM-DD.jsonl
        
        if not self.logs_dir.exists():
            return 0, 0
        
        # Get all JSONL files, sorted by date
        log_files = sorted(self.logs_dir.glob("*.jsonl"))
        
        if not log_files:
            return 0, 0
        
        last_ts = state.get(session_key, 0)
        total_entries = 0
        total_hours = 0
        
        for log_file in log_files:
            # Read entries from this log file
            entries = []
            try:
                with open(log_file, 'r') as f:
                    for line in f:
                        if line.strip():
                            try:
                                entry = json.loads(line)
                                entries.append(entry)
                            except json.JSONDecodeError:
                                continue
            except IOError:
                continue
            
            if not entries:
                continue
            
            # Group by hour
            hourly_data = self.group_by_hour(entries, last_ts)
            
            if not hourly_data:
                continue
            
            # Determine output file from log file name
            date = log_file.stem  # YYYY-MM-DD
            output_file = self.output_dir / f"{date}.md"
            
            # Get existing hours to avoid duplicates
            existing_hours = self.get_existing_hours(output_file)
            
            # Write summaries for each hour
            hours_written = 0
            with open(output_file, 'a') as f:
                for hour_key in sorted(hourly_data.keys()):
                    # Skip if already written
                    if hour_key in existing_hours:
                        continue
                    
                    # Skip if not meaningful
                    if not self.is_meaningful_hour(hourly_data[hour_key]):
                        continue
                    
                    summary = self.format_hour_summary(hour_key, hourly_data[hour_key])
                    f.write(summary)
                    hours_written += 1
            
            total_entries += len(entries)
            total_hours += hours_written
            
            # Update last_ts from this log file
            if entries:
                max_ts = max(e.get('ts', 0) for e in entries)
                last_ts = max(last_ts, max_ts)
        
        # Update state
        state[session_key] = last_ts
        
        return total_entries, total_hours

    def run(self, session_key: Optional[str] = None):
        """Main entry point."""
        state = self.load_state()
        
        if session_key:
            # Process single session
            entries, hours = self.process_session(session_key, state)
            print(f"Processed {entries} entries, wrote {hours} hour summaries for {session_key}")
        else:
            # Process all sessions (in this case, all log files)
            # Use a default session key since we're processing date-based logs
            default_key = "default"
            entries, hours = self.process_session(default_key, state)
            print(f"Processed {entries} entries, wrote {hours} hour summaries")
        
        self.save_state(state)


def main():
    parser = argparse.ArgumentParser(
        description="Convert conversation JSONL logs to structured hourly summaries"
    )
    parser.add_argument(
        '--workspace',
        required=True,
        help='Path to workspace root'
    )
    parser.add_argument(
        '--session-key',
        help='Process only this session key (optional)'
    )
    
    args = parser.parse_args()
    
    summarizer = HourlySummarizer(args.workspace)
    summarizer.run(args.session_key)


if __name__ == '__main__':
    main()
