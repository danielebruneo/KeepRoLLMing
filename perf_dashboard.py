#!/usr/bin/env python3
"""
Terminal dashboard for real-time performance monitoring.
Watches summary.yaml from the performance module and displays metrics in a table.

Usage:
    python perf_dashboard.py                    # Auto-detects PERFORMANCE_LOGS_DIR
    python perf_dashboard.py /path/to/summary   # Specify custom path
"""

import os
import sys
import time
import yaml
import shutil
import select
import fcntl
import termios
import threading
import math
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

# Terminal width configuration - adjust this to fit your terminal
WIDTH = 130  # Total terminal width in characters


def _format_number(value: Any, precision: int = 1) -> str:
    """Render unavailable or non-finite metric values without inventing zero."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(numeric):
        return "n/a"
    return f"{numeric:.{precision}f}"


class PerformanceDashboard:
    """Real-time dashboard for monitoring model performance."""

    def __init__(self, summary_path: str):
        self.summary_path = Path(summary_path)
        self.models: List[Dict[str, Any]] = []
        self.last_mtime = 0.0

    def load_summary(self) -> bool:
        """Load and parse the summary.yaml file."""
        try:
            if not self.summary_path.exists():
                return False

            # Check for file changes using mtime
            current_mtime = self.summary_path.stat().st_mtime
            if current_mtime == self.last_mtime:
                return False

            self.last_mtime = current_mtime

            with open(self.summary_path, 'r') as f:
                data = yaml.safe_load(f)

            self.models = data.get('models', []) if isinstance(data, dict) else []
            return True
        except Exception:
            return False

    def clear_screen(self):
        """Clear terminal screen and move cursor to top."""
        print("\033[2J\033[H", end="")

    def draw_header(self, last_updated: Optional[str] = None):
        """Draw the dashboard header."""
        title = "📊 PERFORMANCE MONITORING DASHBOARD"
        subtitle = "Real-time metrics by model"

        print("=" * WIDTH)
        print(f"{title:^{WIDTH}}")
        print(f"{subtitle:^{WIDTH}}")
        print("=" * WIDTH)

        if last_updated:
            print(f"📅 Last updated: {last_updated}")

    def draw_table_header(self):
        """Draw table header row."""
        # Calculate column widths to fit within WIDTH
        route_width = 18
        hierarchy_width = 42
        model_width = 24


        header = (
            f"{'Route':<{route_width}} | "
            f"{'Hierarchy':<{hierarchy_width}} | "
            f"{'Model':<{model_width}} | "
            f"{'Reqs':>6} | "
            f"{'Req/hr':>9}"
        )
        # print(header)
        print("=" * WIDTH)

    def draw_model_row(self, model: Dict[str, Any]):
        """Draw a single model row in the table."""
        stats = model.get('total_tps', {})
        comp_stats = model.get('completion_tps', {})
        prompt_stats = model.get('prompt_tps', {})
        ttft_stats = model.get('ttft_ms', {})
        elapsed_ms_stats = model.get('elapsed_ms', {})
        comp_tokens_stats = model.get('completion_tokens', {})
        prompt_tokens_stats = model.get('prompt_tokens', {})

        # ExecutionUsage metrics (Phase 12)
        upstream_stats = model.get('upstream_attempts', {})
        reported_stats = model.get('usage_reported_attempts', {})
        recovery_stats = model.get('recovery_count', {})
        ratio_stats = model.get('retry_amplification_ratio', {})
        usage_complete_pct = model.get('usage_complete_pct', 0.0)

        # Truncate model name to fit column width
        route_width = 18
        hierarchy_width = 42
        model_width = 24

        route_name = model.get('route_name', 'unknown')[:route_width]
        route_hierarchy = model.get('route_hierarchy', [route_name])
        if isinstance(route_hierarchy, list):
            hierarchy_str = " -> ".join(route_hierarchy)
        else:
            hierarchy_str = str(route_hierarchy)
        hierarchy_str = hierarchy_str[:hierarchy_width]
        model_name = model.get('model', 'unknown')[:model_width]
        requests = model.get('requests', 0)

        # This is an observed completion rate across the recorded time window,
        # not a capacity estimate derived from request count and mean latency.
        req_per_hour = model.get('requests_per_hour')

        tps_avg = stats.get('avg', 0) or 0
        comp_tps_avg = comp_stats.get('avg', 0) or 0
        prompt_tps_avg = prompt_stats.get('avg', 0) or 0
        total_time_s = (elapsed_ms_stats.get('avg', 0) or 0) / 1000.0 if elapsed_ms_stats else 0.0
        ttft_avg = ttft_stats.get('avg', 0) or 0
        comp_tokens_avg = comp_tokens_stats.get('avg', 0) or 0
        prompt_tokens_avg = prompt_tokens_stats.get('avg', 0) or 0

        # ExecutionUsage values
        upstream_avg = upstream_stats.get('avg', 0) or 0
        recovery_avg = recovery_stats.get('avg', 0) or 0
        ratio_avg = ratio_stats.get('avg')

        row = (
            f"{route_name:<{route_width}} | "
            f"{hierarchy_str:<{hierarchy_width}} | "
            f"{model_name:<{model_width}} | "
            f"Reqs: {requests:>3} | "
            f"Reqs/h: {_format_number(req_per_hour):>7}"
        )
        
        # Add data availability indicator
        if stats.get('avg') is None and requests > 0:
            row += " ⚠️ No TPS (missing token data)"
        elif stats.get('avg') == 0 and requests > 0:
            row += " ⚠️ TPS=0 (check metrics)"

        print("=" * WIDTH)
        print(row)
        # Add ExecutionUsage summary on next line
        if requests > 0:
            print(
                f"   └─ Upstream: {upstream_avg:.1f} avg | Recovery: {recovery_avg:.1f} | "
                f"Retry Amp: {_format_number(ratio_avg, 2)} | "
                f"Usage Complete: {usage_complete_pct*100:.0f}%"
            )
        print("=" * WIDTH)

    def draw_model_detailed_stats(self, model: Dict[str, Any]):
        """Draw detailed per-model statistics (Avg/Min/Max rows)."""
        stats = model.get('total_tps', {})
        comp_stats = model.get('completion_tps', {})
        prompt_stats = model.get('prompt_tps', {})
        ttft_stats = model.get('ttft_ms', {})
        elapsed_ms_stats = model.get('elapsed_ms', {})
        comp_tokens_stats = model.get('completion_tokens', {})
        prompt_tokens_stats = model.get('prompt_tokens', {})

        print(f"{'-' * WIDTH}")
        print(f"Metric | {'Total TPS':>10} | {'Comp TPS':>10} | {'Prompt TPS':>12} | {'Total Time (s)':>15} | {'TTFT (ms)':>12} | {'Comp Tks':>11} | {'Prompt Tks':>13}")
        print("-" * WIDTH)

        # Helper to extract all stats for a metric type
        def get_stats(metric_dict, key='avg'):
            if not metric_dict:
                return 0
            return metric_dict.get(key, 0) or 0

        # Row: Avg values across all metrics
        print(f"{'Avg':<6} | {get_stats(stats, 'avg'):>10.2f} | {get_stats(comp_stats, 'avg'):>10.2f} | {get_stats(prompt_stats, 'avg'):>12.2f} | {(elapsed_ms_stats.get('avg', 0) or 0)/1000:>15.1f} | {get_stats(ttft_stats, 'avg'):>12.2f} | {get_stats(comp_tokens_stats, 'avg'):>11.0f} | {get_stats(prompt_tokens_stats, 'avg'):>13.0f}")

        # Row: Min values across all metrics
        print(f"{'Min':<6} | {get_stats(stats, 'min'):>10.2f} | {get_stats(comp_stats, 'min'):>10.2f} | {get_stats(prompt_stats, 'min'):>12.2f} | {(elapsed_ms_stats.get('min', 0) or 0)/1000:>15.1f} | {get_stats(ttft_stats, 'min'):>12.2f} | {get_stats(comp_tokens_stats, 'min'):>11.0f} | {get_stats(prompt_tokens_stats, 'min'):>13.0f}")

        # Row: Max values across all metrics
        print(f"{'Max':<6} | {get_stats(stats, 'max'):>10.2f} | {get_stats(comp_stats, 'max'):>10.2f} | {get_stats(prompt_stats, 'max'):>12.2f} | {(elapsed_ms_stats.get('max', 0) or 0)/1000:>15.1f} | {get_stats(ttft_stats, 'max'):>12.2f} | {get_stats(comp_tokens_stats, 'max'):>11.0f} | {get_stats(prompt_tokens_stats, 'max'):>13.0f}")
        print("-" * WIDTH)


    def draw_footer(self, routes: List[Dict[str, Any]]):
        """Draw dashboard footer with summary stats."""
        print("-" * WIDTH)

        successful = [r for r in routes if r.get('requests', 0) > 0]

        if successful:
            total_requests = sum(r.get('requests', 0) for r in successful)

            # Calculate weighted average TPS
            total_tps_weighted = 0
            total_tps_count = 0

            for route in successful:
                tps = route.get('total_tps', {}).get('avg', 0)
                requests = route.get('requests', 0)
                if tps and requests > 0:
                    total_tps_weighted += tps * requests
                    total_tps_count += requests

            avg_tps = total_tps_weighted / total_tps_count if total_tps_count > 0 else 0

            # Calculate weighted average TTFT and prompt tokens
            total_ttft_weighted = 0
            total_ttft_count = 0
            total_prompt_weighted = 0
            total_prompt_count = 0
            total_elapsed_weighted = 0
            total_elapsed_count = 0

            for route in successful:
                ttft = route.get('ttft_ms', {}).get('avg', 0)
                requests = route.get('requests', 0)
                if ttft and requests > 0:
                    total_ttft_weighted += ttft * requests
                    total_ttft_count += requests

                prompt = route.get('prompt_tokens', {}).get('avg', 0)
                requests = route.get('requests', 0)
                if prompt and requests > 0:
                    total_prompt_weighted += prompt * requests
                    total_prompt_count += requests

                elapsed_ms = route.get('elapsed_ms', {}).get('avg', 0)
                requests = route.get('requests', 0)
                if elapsed_ms and requests > 0:
                    total_elapsed_weighted += elapsed_ms * requests
                    total_elapsed_count += requests

            avg_ttft = total_ttft_weighted / total_ttft_count if total_ttft_count > 0 else 0
            avg_prompt = total_prompt_weighted / total_prompt_count if total_prompt_count > 0 else 0
            avg_elapsed_ms = total_elapsed_weighted / total_elapsed_count if total_elapsed_count > 0 else 0

            print("=" * WIDTH)
            print("📊 Summary:")
            print("-" * WIDTH)
            print(f"   Total Requests: {total_requests}  |  Avg TPS: {avg_tps:.2f}  |  Avg TTFT: {avg_ttft:.2f}ms  |  Avg Prompt Tks: {avg_prompt:.0f}  |  Avg Total Time: {avg_elapsed_ms/1000:.2f}s")
            print("-" * WIDTH)

        print("\n💡 Press Ctrl+C or 'q' to exit")
        print("💡 Press 'c' to clear logs | 'a' to archive | 's' to save summary")
        print("=" * WIDTH)

    def reset_logs(self):
        """Clear the __perf_logs directory to reset data."""
        logs_dir = Path(os.getenv("PERFORMANCE_LOGS_DIR", "./__performance_logs"))
        if logs_dir.exists():
            # Clear all files in the directory, but keep the directory itself
            for item in logs_dir.iterdir():
                try:
                    if item.is_file() or item.is_symlink():
                        item.unlink()
                    elif item.is_dir():
                        # Don't delete 'archive' folder if it exists inside logs_dir
                        if item.name != "archive":
                            shutil.rmtree(item)
                except Exception as e:
                    print(f"⚠️ Error deleting {item}: {e}")
            
            self.models = []  # Clear cached models
            print(f"🗑️  Cleared logs in directory: {logs_dir}")
        else:
            print(f"⚠️  Logs directory not found: {logs_dir}")

    def save_summary(self):
        """Force regenerate summary.yaml from JSONL files and print location."""
        logs_dir = Path(os.getenv("PERFORMANCE_LOGS_DIR", "./__performance_logs"))
        try:
            from .keeprollming.performance import _update_summary
            _update_summary(logs_dir)
            print(f"✨ Summary saved to: {logs_dir / 'summary.yaml'}")
        except Exception as e:
            print(f"❌ Failed to save summary: {e}")

    def archive_logs(self):
        """Archive the current summary and reset logs."""
        logs_dir = Path(os.getenv("PERFORMANCE_LOGS_DIR", "./__performance_logs"))
        if not self.summary_path.exists():
            print("⚠️  No summary file to archive.")
            return

        # 1. Create 'archive' directory inside logs_dir if it doesn't exist
        archive_dir = logs_dir / "archive"
        archive_dir.mkdir(exist_ok=True)

        # 2. Move the current summary.yaml to the archive folder with a timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archived_summary = archive_dir / f"summary_{timestamp}.yaml"
        
        try:
            shutil.move(str(self.summary_path), str(archived_summary))
            print(f"📦 Summary archived to: {archived_summary}")
            
            # 3. Reset the rest of the logs (clearing other files in logs_dir)
            self.reset_logs()
            print("✨ Archive and reset complete.")
        except Exception as e:
            print(f"❌ Failed to archive: {e}")

    def render(self):
        """Render the complete dashboard."""
        updated = self.load_summary()

        if not updated and self.models:
            # No new data but we have cached data, still render
            pass

        self.clear_screen()

        last_updated = None
        if self.summary_path.exists():
            try:
                with open(self.summary_path, 'r') as f:
                    data = yaml.safe_load(f)
                last_updated = data.get('updated_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            except Exception:
                pass

        self.draw_header(last_updated)

        if not self.models:
            print("\n⏳ Waiting for performance data...")
            print("   (Make sure the performance module is running and writing to summary.yaml)")
        else:
            self.draw_table_header()

            # Flatten all routes from model groups into a single list
            all_routes = []
            for model in self.models:
                if 'routes' in model:
                    # This is a model group with nested routes
                    all_routes.extend(model.get('routes', []))
                else:
                    # This is a direct route
                    all_routes.append(model)

            # Sort by requests (most active first)
            sorted_routes = sorted(all_routes, key=lambda r: r.get('requests', 0), reverse=True)

            for route in sorted_routes:
                self.draw_model_row(route)
                self.draw_model_detailed_stats(route)

            self.draw_footer(sorted_routes)

    def watch(self, interval: float = 5.0, batch_mode: bool = False):
        """Continuously watch for changes and update the dashboard."""
        if batch_mode:
            # Batch mode: render once and exit without TTY handling
            self.render()
            return

        # Save terminal settings
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)

        try:
            # Enable raw mode temporarily for key capture
            new_settings = termios.tcgetattr(fd)
            new_settings[3] &= ~(termios.ECHO | termios.ICANON)
            new_settings[6][termios.VMIN] = 0
            new_settings[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSADRAIN, new_settings)

            try:
                while True:
                    self.render()
                    # Check for keypress between renders (non-blocking)
                    if select.select([fd], [], [], 0.05) == ([fd], [], []):
                        ch = os.read(fd, 1).decode('utf-8', errors='ignore')
                        if ch == 'q':
                            print("\n\n👋 Dashboard stopped.")
                            sys.exit(0)
                        elif ch == 'c':
                            self.reset_logs()
                        elif ch == 'a':
                            self.archive_logs()
                        elif ch == 's':
                            self.save_summary()
                    time.sleep(interval)
            except KeyboardInterrupt:
                print("\n\n👋 Dashboard stopped.")
        finally:
            # Restore terminal settings
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Performance Dashboard')
    parser.add_argument('summary_path', nargs='?', help='Path to summary.yaml file')
    parser.add_argument('--batch', '-b', action='store_true', help='Batch mode: render once and exit')
    parser.add_argument('--interval', '-i', type=float, default=5.0, help='Refresh interval in seconds (default: 5.0)')
    
    args = parser.parse_args()
    
    # Determine summary path from environment or CLI argument or default
    perf_logs_dir = os.getenv("PERFORMANCE_LOGS_DIR", "./__performance_logs")
    summary_path = os.path.join(perf_logs_dir, "summary.yaml")
    
    if args.summary_path:
        summary_path = args.summary_path
    
    dashboard = PerformanceDashboard(summary_path)
    
    if args.batch:
        print(f"🔍 Batch mode: {summary_path}")
        dashboard.render()
    else:
        print(f"🔍 Watching: {summary_path}")
        print("💡 Press Ctrl+C to exit (use --batch for single render)")
        dashboard.watch(interval=args.interval, batch_mode=args.batch)


if __name__ == "__main__":
    main()
