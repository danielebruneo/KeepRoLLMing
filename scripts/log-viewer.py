#!/usr/bin/env python3
"""
BASIC_PLAIN log viewer — reads keeprollming.log.json (NDJSON) and renders
human-readable BASIC_PLAIN output.

Usage:
    python scripts/log-viewer.py [--tail] [--file keeprollming.log.json]
    python scripts/log-viewer.py --plain-output keeprollming.log  # Save to file
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from keeprollming.logging.plain_text import format_plain
except ImportError:
    print("Warning: format_plain not available, using raw output", file=sys.stderr)
    format_plain = None


def render_plain(rec: dict) -> str | None:
    """Render a single JSON log record as BASIC_PLAIN text."""
    if format_plain is None:
        return json.dumps(rec)
    try:
        return format_plain(rec)
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description="BASIC_PLAIN log viewer")
    parser.add_argument("--file", "-f", default="keeprollming.log.json",
                        help="NDJSON log file to read")
    parser.add_argument("--tail", "-t", action="store_true",
                        help="Follow the file (like tail -f)")
    parser.add_argument("--plain-output", "-o",
                        help="Write BASIC_PLAIN to this file")
    parser.add_argument("--filter-event", help="Only show events matching this type")
    args = parser.parse_args()

    log_path = args.file
    if not os.path.isabs(log_path):
        # Try LOG_PATH env, then current dir
        log_dir = os.environ.get("LOG_PATH", ".")
        log_path = os.path.join(log_dir, os.path.basename(log_path))

    out = open(args.plain_output, "w") if args.plain_output else sys.stdout

    if not os.path.exists(log_path):
        print(f"Log file not found: {log_path}", file=sys.stderr)
        sys.exit(1)

    with open(log_path, "r") as f:
        if args.tail:
            # Seek to end, then follow
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    rec = json.loads(line.strip())
                    if not args.filter_event or rec.get("msg") == args.filter_event:
                        rendered = render_plain(rec)
                        if rendered:
                            out.write(rendered + "\n")
                            out.flush()
                else:
                    time.sleep(0.1)
        else:
            # Read all at once
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if args.filter_event and rec.get("msg") != args.filter_event:
                    continue
                rendered = render_plain(rec)
                if rendered:
                    out.write(rendered + "\n")

    if args.plain_output:
        out.close()
        print(f"BASIC_PLAIN written to {args.plain_output}")


if __name__ == "__main__":
    main()
