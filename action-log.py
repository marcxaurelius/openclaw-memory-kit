#!/usr/bin/env python3
"""
Action Logger - Duplicate Prevention System

Logs actions to JSONL and checks for duplicates within time windows.

Usage:
    action-log.py log --type <type> --target <target> --summary <summary> [--session <session>] [--status <status>]
    action-log.py check --type <type> --target <target> --summary <summary> [--window <hours>]
    action-log.py update --ts <timestamp> --status <status>
    action-log.py --help
"""

import json
import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional


class ActionLogger:
    def __init__(self, workspace_root: Path):
        self.workspace = workspace_root
        self.logs_dir = workspace_root / "logs"
        self.action_log = self.logs_dir / "actions.jsonl"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
    
    def parse_timestamp(self, ts: str) -> datetime:
        """Parse ISO timestamp."""
        # Normalize: remove timezone indicators and fractional seconds
        ts_normalized = ts.replace('+00:00', '').replace('Z', '')
        if '.' in ts_normalized:
            ts_normalized = ts_normalized.split('.')[0]
        
        # Parse as basic ISO format
        try:
            return datetime.strptime(ts_normalized, "%Y-%m-%dT%H:%M:%S")
        except ValueError as e:
            raise ValueError(f"Could not parse timestamp: {ts}") from e
    
    def format_timestamp(self, dt: datetime) -> str:
        """Format datetime as ISO timestamp."""
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    
    def log_action(self, action_type: str, target: str, summary: str, 
                   session: Optional[str] = None, status: str = "pending") -> Dict:
        """Log a new action."""
        now = datetime.now()
        
        record = {
            "ts": self.format_timestamp(now),
            "type": action_type,
            "target": target,
            "summary": summary,
            "session": session or now.strftime("%Y-%m-%d-%H%M%S"),
            "status": status
        }
        
        with open(self.action_log, 'a') as f:
            f.write(json.dumps(record) + '\n')
        
        return record
    
    def read_actions(self) -> List[Dict]:
        """Read all actions from log."""
        if not self.action_log.exists():
            return []
        
        actions = []
        with open(self.action_log, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        actions.append(json.loads(line))
                    except json.JSONDecodeError:
                        print(f"Warning: Could not parse line: {line[:50]}...", file=sys.stderr)
        
        return actions
    
    def check_duplicate(self, action_type: str, target: str, summary: str, 
                       window_hours: float = 2.0) -> Optional[Dict]:
        """Check if action is a duplicate within time window.
        
        Returns the duplicate action if found, None otherwise.
        """
        actions = self.read_actions()
        now = datetime.now()
        cutoff = now - timedelta(hours=window_hours)
        
        for action in reversed(actions):  # Check most recent first
            try:
                action_time = self.parse_timestamp(action['ts'])
            except (ValueError, KeyError):
                continue
            
            # Skip actions outside time window
            if action_time < cutoff:
                break
            
            # Check if it matches
            if (action.get('type') == action_type and 
                action.get('target') == target and 
                action.get('summary') == summary):
                return action
        
        return None
    
    def update_status(self, timestamp: str, status: str) -> bool:
        """Update status of an action by timestamp."""
        if not self.action_log.exists():
            return False
        
        actions = self.read_actions()
        updated = False
        
        # Update matching record
        for action in actions:
            if action.get('ts') == timestamp:
                action['status'] = status
                updated = True
                break
        
        if not updated:
            return False
        
        # Rewrite file
        with open(self.action_log, 'w') as f:
            for action in actions:
                f.write(json.dumps(action) + '\n')
        
        return True


def main():
    parser = argparse.ArgumentParser(
        description="Action Logger - Duplicate prevention system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # Log command
    log_parser = subparsers.add_parser('log', help='Log a new action')
    log_parser.add_argument('--type', required=True, 
                           choices=['discord_post', 'dm', 'email', 'api_call', 'file_create', 'webhook', 'other'],
                           help='Action type')
    log_parser.add_argument('--target', required=True, help='Target (channel, user, etc.)')
    log_parser.add_argument('--summary', required=True, help='Action summary')
    log_parser.add_argument('--session', help='Session identifier')
    log_parser.add_argument('--status', default='pending', choices=['pending', 'success', 'failed'],
                           help='Action status (default: pending)')
    
    # Check command
    check_parser = subparsers.add_parser('check', help='Check for duplicate action')
    check_parser.add_argument('--type', required=True, 
                             choices=['discord_post', 'dm', 'email', 'api_call', 'file_create', 'webhook', 'other'],
                             help='Action type')
    check_parser.add_argument('--target', required=True, help='Target (channel, user, etc.)')
    check_parser.add_argument('--summary', required=True, help='Action summary')
    check_parser.add_argument('--window', type=float, default=2.0, 
                             help='Time window in hours (default: 2)')
    
    # Update command
    update_parser = subparsers.add_parser('update', help='Update action status')
    update_parser.add_argument('--ts', required=True, help='Timestamp of action to update')
    update_parser.add_argument('--status', required=True, choices=['pending', 'success', 'failed'],
                              help='New status')
    
    parser.add_argument('--workspace', default='/home/steve/.openclaw/workspace', 
                       help='Workspace root path')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    logger = ActionLogger(Path(args.workspace))
    
    if args.command == 'log':
        record = logger.log_action(args.type, args.target, args.summary, args.session, args.status)
        print(json.dumps(record))
        sys.exit(0)
    
    elif args.command == 'check':
        duplicate = logger.check_duplicate(args.type, args.target, args.summary, args.window)
        if duplicate:
            print(json.dumps(duplicate))
            sys.exit(1)  # Exit code 1 = duplicate found
        else:
            print("No duplicate found")
            sys.exit(0)  # Exit code 0 = clean
    
    elif args.command == 'update':
        success = logger.update_status(args.ts, args.status)
        if success:
            print(f"Updated status to {args.status}")
            sys.exit(0)
        else:
            print(f"Error: Could not find action with timestamp {args.ts}", file=sys.stderr)
            sys.exit(1)


if __name__ == '__main__':
    main()
