#!/usr/bin/env python3
"""
Memory Index Generator

Generates a compact categorized index of MEMORY.md for quick session scanning.
Reduces token load by ~70% compared to loading full MEMORY.md.
"""

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Tuple


class MemoryIndexer:
    """Parse MEMORY.md and generate categorized index."""
    
    def __init__(self, workspace_path: str):
        self.workspace = Path(workspace_path)
        self.memory_file = self.workspace / "MEMORY.md"
        self.output_file = self.workspace / "memory" / "memory-index.md"
        
        # Category patterns
        self.patterns = {
            'gotcha': [
                r'\bnever\b', r'\bdon\'t\b', r'\bwarning\b', r'\bcareful\b',
                r'\bNOT\b', r'\bbroken\b', r'\balways check\b', r'\bavoid\b'
            ],
            'decision': [
                r'\bchose\b', r'\bdecided\b', r'\bgoing with\b', r'\bover\b',
                r'\binstead of\b', r'\bapproach:', r'\bpath\b'
            ],
            'discovery': [
                r'\bdiscovered\b', r'\bfound that\b', r'\bturns out\b',
                r'\blearned that\b', r'\brealized\b'
            ],
            'lesson': [
                r'\blesson:', r'\bdon\'t repeat\b', r'\bremember to\b',
                r'\bnext time\b', r'\bmistake\b'
            ],
            'problem_fix': [
                r'\bfix:', r'\bsolved by\b', r'\bworkaround:', r'\bissue:',
                r'→', r'\bproblem\b.*\bsolution\b'
            ]
        }
        
        # Compiled patterns
        self.compiled = {
            cat: [re.compile(p, re.IGNORECASE) for p in patterns]
            for cat, patterns in self.patterns.items()
        }
    
    def read_memory(self) -> str:
        """Read MEMORY.md content."""
        if not self.memory_file.exists():
            return ""
        return self.memory_file.read_text(encoding='utf-8')
    
    def categorize_line(self, line: str, section: str) -> str:
        """Determine category for a line based on keywords and section."""
        line_lower = line.lower()
        
        # Section-based categorization (priority)
        if section in ['never forget', '🚨 never forget']:
            return 'gotcha'
        if section in ['lessons learned']:
            return 'lesson'
        if section in ['active now', 'on deck', 'goals', 'primary', 'secondary', 'tertiary', 'milestones']:
            return 'active'
        
        # Keyword-based categorization
        scores = {cat: 0 for cat in self.compiled.keys()}
        for cat, patterns in self.compiled.items():
            for pattern in patterns:
                if pattern.search(line):
                    scores[cat] += 1
        
        max_score = max(scores.values())
        if max_score > 0:
            return max(scores, key=scores.get)
        
        # Default: if it's in expertise/context, treat as active
        if section in ['marc\'s expertise', 'context']:
            return 'active'
        
        return None
    
    def extract_entries(self, content: str) -> Dict[str, List[str]]:
        """Parse MEMORY.md and extract categorized entries."""
        entries = {
            'gotcha': [],
            'decision': [],
            'discovery': [],
            'lesson': [],
            'problem_fix': [],
            'active': []
        }
        
        if not content.strip():
            return entries
        
        lines = content.split('\n')
        current_section = ""
        
        for line in lines:
            stripped = line.strip()
            
            # Track section headers
            if stripped.startswith('#'):
                current_section = re.sub(r'^#+\s*', '', stripped).lower()
                current_section = re.sub(r'[🚨🔴🟤🟣🔵🟡🟢]', '', current_section).strip()
                continue
            
            # Skip empty lines, horizontal rules, metadata
            if not stripped or stripped.startswith('---') or stripped.startswith('_'):
                continue
            
            # Skip markdown formatting lines
            if stripped.startswith('**') and stripped.endswith('**'):
                continue
            
            # Process bullet points and meaningful text
            if stripped.startswith('-') or stripped.startswith('•'):
                text = re.sub(r'^[-•]\s*', '', stripped)
                text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)  # Remove bold
                
                # Skip very short entries
                if len(text) < 15:
                    continue
                
                category = self.categorize_line(text, current_section)
                if category and category in entries:
                    # Truncate to 100 chars
                    if len(text) > 100:
                        text = text[:97] + "..."
                    entries[category].append(text)
        
        return entries
    
    def generate_index(self, entries: Dict[str, List[str]]) -> str:
        """Generate formatted index markdown."""
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        total_entries = sum(len(v) for v in entries.values())
        
        output = [
            "# Memory Index",
            f"Generated: {timestamp} | Entries: {total_entries} | Source: MEMORY.md",
            "",
            "<!-- DO NOT EDIT MANUALLY — Generated artifact -->",
            "<!-- Use entry IDs (G01, D01, etc.) to find full content in MEMORY.md -->",
            ""
        ]
        
        # Category config
        categories = [
            ('gotcha', '🔴 Gotchas', 'G'),
            ('decision', '🟤 Decisions', 'D'),
            ('discovery', '🟣 Discoveries', 'V'),
            ('lesson', '🔵 Lessons', 'L'),
            ('problem_fix', '🟡 Problem-Fix Pairs', 'P'),
            ('active', '🟢 Active Context', 'A')
        ]
        
        for key, title, prefix in categories:
            items = entries.get(key, [])
            if items:
                output.append(f"## {title}")
                for idx, item in enumerate(items, 1):
                    entry_id = f"{prefix}{idx:02d}"
                    output.append(f"- {entry_id}: {item}")
                output.append("")
        
        return '\n'.join(output)
    
    def run(self) -> Tuple[bool, str, int]:
        """Execute indexing. Returns (success, output_path, char_count)."""
        content = self.read_memory()
        entries = self.extract_entries(content)
        index_content = self.generate_index(entries)
        
        # Ensure output directory exists
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Write output
        self.output_file.write_text(index_content, encoding='utf-8')
        
        char_count = len(index_content)
        return True, str(self.output_file), char_count


def main():
    parser = argparse.ArgumentParser(
        description='Generate compact categorized index of MEMORY.md',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --workspace /home/steve/.openclaw/workspace
  %(prog)s -w /path/to/workspace

Categories:
  🔴 Gotchas       - Things that are wrong/dangerous/misleading
  🟤 Decisions     - Choices made with reasoning
  🟣 Discoveries   - Things found out
  🔵 Lessons       - Meta-lessons about how to work
  🟡 Problem-Fix   - Problem→solution pairs
  🟢 Active Context - Current state, active projects, goals
        """
    )
    
    parser.add_argument(
        '--workspace', '-w',
        required=True,
        help='Path to OpenClaw workspace directory'
    )
    
    args = parser.parse_args()
    
    indexer = MemoryIndexer(args.workspace)
    success, output_path, char_count = indexer.run()
    
    if success:
        token_estimate = char_count // 4
        print(f"✓ Generated: {output_path}")
        print(f"  Size: {char_count} chars (~{token_estimate} tokens)")
        
        if char_count > 16000:
            print(f"  ⚠ Warning: Index exceeds 16K char target")
        
        return 0
    else:
        print("✗ Failed to generate index", file=sys.stderr)
        return 1


if __name__ == '__main__':
    import sys
    sys.exit(main())
