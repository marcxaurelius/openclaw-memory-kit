#!/usr/bin/env python3
"""
Post-Compaction Context Recovery Injector

Assembles a context recovery payload from multiple sources after context compaction.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional


def estimate_tokens(text: str) -> int:
    """Estimate tokens as chars/4"""
    return len(text) // 4


def find_latest_jsonl(workspace: Path, session_key: Optional[str] = None) -> Optional[Path]:
    """Find the most recent JSONL log file"""
    logs_dir = workspace / "logs"
    if not logs_dir.exists():
        return None
    
    jsonl_files = list(logs_dir.glob("*.jsonl"))
    if not jsonl_files:
        return None
    
    if session_key:
        # Filter by session key if provided
        matching = [f for f in jsonl_files if session_key in f.name]
        if matching:
            return max(matching, key=lambda f: f.stat().st_mtime)
    
    # Return most recent by modification time
    return max(jsonl_files, key=lambda f: f.stat().st_mtime)


def extract_text_from_content(content: Any) -> str:
    """Extract text from content field (handles string or array of blocks)"""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "thinking":
                    text_parts.append(f"[THINKING] {block.get('thinking', '')}")
        return "\n".join(text_parts)
    return ""


def load_jsonl_messages(jsonl_path: Path) -> Dict[str, List[Dict[str, Any]]]:
    """Load and categorize messages from JSONL"""
    messages = {
        "user": [],
        "assistant": [],
        "thinking": []
    }
    
    if not jsonl_path or not jsonl_path.exists():
        return messages
    
    try:
        with open(jsonl_path, 'r') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    role = record.get("role", "")
                    content = record.get("content", "")
                    
                    # Extract thinking blocks
                    if role == "thinking":
                        messages["thinking"].append(record)
                    elif role == "assistant" and isinstance(content, list):
                        # Check for thinking blocks in assistant messages
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "thinking":
                                messages["thinking"].append({
                                    "seq": record.get("seq"),
                                    "ts": record.get("ts"),
                                    "role": "thinking",
                                    "content": block.get("thinking", "")
                                })
                    
                    # Store user and assistant messages
                    if role == "user":
                        messages["user"].append(record)
                    elif role == "assistant":
                        messages["assistant"].append(record)
                        
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        print(f"Warning: Error reading JSONL: {e}", file=sys.stderr)
    
    return messages


def load_hourly_summaries(workspace: Path, hours: int = 24) -> List[tuple]:
    """Load hourly summaries from the last N hours"""
    hourly_dir = workspace / "memory" / "hourly"
    if not hourly_dir.exists():
        return []
    
    cutoff = datetime.now() - timedelta(hours=hours)
    summaries = []
    
    for file in hourly_dir.glob("*.md"):
        try:
            # Parse timestamp from filename (format: YYYY-MM-DD-HH.md)
            parts = file.stem.split('-')
            if len(parts) >= 4:
                year, month, day, hour = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
                file_time = datetime(year, month, day, hour)
                
                if file_time >= cutoff:
                    with open(file, 'r') as f:
                        content = f.read()
                    summaries.append((file_time, content))
        except (ValueError, IndexError):
            continue
    
    # Sort by timestamp, newest first
    summaries.sort(key=lambda x: x[0], reverse=True)
    return summaries


def load_memory_md(workspace: Path) -> str:
    """Load MEMORY.md content"""
    memory_file = workspace / "MEMORY.md"
    if not memory_file.exists():
        return ""
    
    try:
        with open(memory_file, 'r') as f:
            return f.read()
    except Exception as e:
        print(f"Warning: Error reading MEMORY.md: {e}", file=sys.stderr)
        return ""


def format_messages(messages: List[Dict[str, Any]], limit: int) -> str:
    """Format messages for output"""
    output = []
    # Take the most recent N messages
    recent = messages[-limit:] if len(messages) > limit else messages
    
    for msg in recent:
        seq = msg.get("seq", "?")
        ts = msg.get("ts", 0)
        timestamp = datetime.fromtimestamp(ts / 1000).strftime("%H:%M:%S") if ts else "unknown"
        content = extract_text_from_content(msg.get("content", ""))
        
        # Truncate very long messages
        if len(content) > 500:
            content = content[:500] + "..."
        
        output.append(f"[{seq}] {timestamp}: {content}")
    
    return "\n".join(output)


def assemble_payload(workspace: Path, session_key: Optional[str], max_chars: int) -> tuple:
    """Assemble the context recovery payload"""
    sections = []
    stats = {
        "hourly_summaries": 0,
        "user_messages": 0,
        "assistant_messages": 0,
        "thinking_blocks": 0
    }
    
    # 1. Load hourly summaries (last 24h)
    hourly_summaries = load_hourly_summaries(workspace, hours=24)
    if hourly_summaries:
        stats["hourly_summaries"] = len(hourly_summaries)
        hourly_text = "## Recent Hourly Summaries (last 24h)\n\n"
        for timestamp, content in hourly_summaries:
            hourly_text += f"### {timestamp.strftime('%Y-%m-%d %H:00')}\n{content}\n\n"
        sections.append(("hourly", hourly_text))
    
    # 2-4. Load JSONL messages
    jsonl_path = find_latest_jsonl(workspace, session_key)
    messages = load_jsonl_messages(jsonl_path)
    
    if messages["user"]:
        stats["user_messages"] = len(messages["user"])
        user_limit = 15
        user_text = f"## Recent User Messages (last {min(user_limit, len(messages['user']))})\n\n"
        user_text += format_messages(messages["user"], user_limit)
        sections.append(("user", user_text))
    
    if messages["assistant"]:
        stats["assistant_messages"] = len(messages["assistant"])
        assistant_limit = 15
        assistant_text = f"## Recent Assistant Messages (last {min(assistant_limit, len(messages['assistant']))})\n\n"
        assistant_text += format_messages(messages["assistant"], assistant_limit)
        sections.append(("assistant", assistant_text))
    
    if messages["thinking"]:
        stats["thinking_blocks"] = len(messages["thinking"])
        thinking_limit = 10
        thinking_text = f"## Recent Thinking Blocks (last {min(thinking_limit, len(messages['thinking']))})\n\n"
        thinking_text += format_messages(messages["thinking"], thinking_limit)
        sections.append(("thinking", thinking_text))
    
    # 5. Load MEMORY.md
    memory_content = load_memory_md(workspace)
    if memory_content:
        memory_text = "## Active Context (from MEMORY.md)\n\n"
        memory_text += memory_content
        sections.append(("memory", memory_text))
    
    # Budget management - trim if needed
    total_chars = sum(len(s[1]) for s in sections)
    
    if total_chars > max_chars:
        # Truncation order: hourly → user/assistant → thinking → MEMORY.md
        
        # 1. Trim hourly summaries (keep most recent 6 hours)
        for i, (section_type, content) in enumerate(sections):
            if section_type == "hourly":
                recent_summaries = hourly_summaries[:6]
                hourly_text = "## Recent Hourly Summaries (last 6h)\n\n"
                for timestamp, sum_content in recent_summaries:
                    # Truncate each summary to 500 chars max
                    truncated = sum_content[:500]
                    if len(sum_content) > 500:
                        truncated += "..."
                    hourly_text += f"### {timestamp.strftime('%Y-%m-%d %H:00')}\n{truncated}\n\n"
                sections[i] = ("hourly", hourly_text)
                stats["hourly_summaries"] = len(recent_summaries)
                break
        
        total_chars = sum(len(s[1]) for s in sections)
        
        # 2. Reduce user/assistant messages to 10 each
        if total_chars > max_chars:
            for i, (section_type, content) in enumerate(sections):
                if section_type == "user":
                    user_text = "## Recent User Messages (last 10)\n\n"
                    user_text += format_messages(messages["user"], 10)
                    sections[i] = ("user", user_text)
                    stats["user_messages"] = min(10, len(messages["user"]))
                elif section_type == "assistant":
                    assistant_text = "## Recent Assistant Messages (last 10)\n\n"
                    assistant_text += format_messages(messages["assistant"], 10)
                    sections[i] = ("assistant", assistant_text)
                    stats["assistant_messages"] = min(10, len(messages["assistant"]))
            
            total_chars = sum(len(s[1]) for s in sections)
        
        # 3. Reduce thinking blocks to 5
        if total_chars > max_chars:
            for i, (section_type, content) in enumerate(sections):
                if section_type == "thinking":
                    thinking_text = "## Recent Thinking Blocks (last 5)\n\n"
                    thinking_text += format_messages(messages["thinking"], 5)
                    sections[i] = ("thinking", thinking_text)
                    stats["thinking_blocks"] = min(5, len(messages["thinking"]))
                    break
            
            total_chars = sum(len(s[1]) for s in sections)
        
        # 4. Truncate MEMORY.md to first 2K chars
        if total_chars > max_chars:
            for i, (section_type, content) in enumerate(sections):
                if section_type == "memory":
                    memory_text = "## Active Context (from MEMORY.md)\n\n"
                    memory_text += memory_content[:2000]
                    if len(memory_content) > 2000:
                        memory_text += "\n\n[truncated]"
                    sections[i] = ("memory", memory_text)
                    break
            
            total_chars = sum(len(s[1]) for s in sections)
        
        # 5. Final aggressive truncation if still over budget
        if total_chars > max_chars:
            # Remove entire sections in reverse priority order until under budget
            priority_order = ["memory", "thinking", "assistant", "user", "hourly"]
            for section_to_remove in priority_order:
                if total_chars <= max_chars:
                    break
                sections = [s for s in sections if s[0] != section_to_remove]
                if section_to_remove == "hourly":
                    stats["hourly_summaries"] = 0
                elif section_to_remove == "user":
                    stats["user_messages"] = 0
                elif section_to_remove == "assistant":
                    stats["assistant_messages"] = 0
                elif section_to_remove == "thinking":
                    stats["thinking_blocks"] = 0
                total_chars = sum(len(s[1]) for s in sections)
    
    # Assemble final output
    output = f"# Post-Compaction Context Recovery\nGenerated: {datetime.now().isoformat()}\n"
    raw_tokens = estimate_tokens(''.join(s[1] for s in sections))
    output += f"Token estimate: ~{raw_tokens / 1000:.1f}K tokens (~{raw_tokens} tokens)\n\n"
    output += "\n\n".join(s[1] for s in sections)
    
    return output, stats


def log_compaction_event(workspace: Path, stats: Dict[str, int], token_estimate: int):
    """Log compaction event to daily note"""
    today = datetime.now().strftime("%Y-%m-%d")
    daily_note = workspace / "memory" / f"{today}.md"
    
    # Create memory directory if it doesn't exist
    daily_note.parent.mkdir(parents=True, exist_ok=True)
    
    time_str = datetime.now().strftime("%H:%M")
    event = f"\n## {time_str} — Compaction Event\n"
    event += f"Context compaction detected. Recovery payload: ~{token_estimate}K tokens, "
    event += f"{stats['hourly_summaries']} hourly summaries, "
    event += f"{stats['user_messages'] + stats['assistant_messages']} recent messages injected.\n"
    
    try:
        with open(daily_note, 'a') as f:
            f.write(event)
    except Exception as e:
        print(f"Warning: Could not log compaction event: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Assemble post-compaction context recovery payload"
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        required=True,
        help="Path to OpenClaw workspace"
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=8000,
        help="Maximum tokens for payload (default: 8000)"
    )
    parser.add_argument(
        "--session-key",
        type=str,
        help="Filter JSONL to specific session"
    )
    parser.add_argument(
        "--log-event",
        action="store_true",
        help="Log compaction event to daily note"
    )
    
    args = parser.parse_args()
    
    # Convert max tokens to max chars
    max_chars = args.max_tokens * 4
    
    # Assemble payload
    payload, stats = assemble_payload(args.workspace, args.session_key, max_chars)
    
    # Log event if requested
    if args.log_event:
        token_estimate = estimate_tokens(payload) // 1000  # Convert to K tokens
        log_compaction_event(args.workspace, stats, token_estimate)
    
    # Output payload
    print(payload)


if __name__ == "__main__":
    main()
