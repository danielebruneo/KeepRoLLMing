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
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime


# Terminal width configuration - adjust this to fit your terminal
WIDTH = 120  # Total terminal width in characters


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
            
            self.models = data.get('models', [])
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
            print(f"\n📅 Last updated: {last_updated}")

    def draw_table_header(self):
        """Draw table header row."""
        # Calculate column widths to fit within WIDTH
        model_width = WIDTH // 3 - 2
        
        header = (
            f"{'Model':<{model_width}} | "
            f"{'Reqs':>6} | "
            f"{'TPS':>7} | "
            f"{'Comp TPS':>9} | "
            f"{'Prompt TPS':>11} | "
            f"{'TTFT (ms)':>10} | "
            f"{'Comp Tks':>8} | "
            f"{'Prompt Tks':>10}"
        )
        print(header)
        print("-" * WIDTH)

    def draw_model_row(self, model: Dict[str, Any]):
        """Draw a single model row in the table."""
        stats = model.get('tps', {})
        comp_stats = model.get('completion_tps', {})
        prompt_stats = model.get('prompt_tps', {})
        ttft_stats = model.get('ttft_ms', {})
        comp_tokens_stats = model.get('completion_tokens', {})
        prompt_tokens_stats = model.get('prompt_tokens', {})

        # Truncate model name to fit column width
        model_width = WIDTH // 3 - 2
        name = model.get('model', 'unknown')[:model_width]
        requests = model.get('requests', 0)

        tps_avg = stats.get('avg', 0) or 0
        comp_tps_avg = comp_stats.get('avg', 0) or 0
        prompt_tps_avg = prompt_stats.get('avg', 0) or 0
        ttft_avg = ttft_stats.get('avg', 0) or 0
        comp_tokens_avg = comp_tokens_stats.get('avg', 0) or 0
        prompt_tokens_avg = prompt_tokens_stats.get('avg', 0) or 0

        row = (
            f"{name:<{model_width}} | "
            f"{requests:>6} | "
            f"{tps_avg:>7.2f} | "
            f"{comp_tps_avg:>9.2f} | "
            f"{prompt_tps_avg:>11.2f} | "
            f"{ttft_avg:>10.2f} | "
            f"{comp_tokens_avg:>8.0f} | "
            f"{prompt_tokens_avg:>10.0f}"
        )
        print(row)

    def draw_footer(self, models: List[Dict[str, Any]]):
        """Draw dashboard footer with summary stats."""
        print("-" * WIDTH)

        successful = [m for m in models if m.get('requests', 0) > 0]

        if successful:
            total_requests = sum(m.get('requests', 0) for m in successful)

            # Calculate weighted average TPS
            total_tps_weighted = 0
            total_tps_count = 0

            for model in successful:
                tps = model.get('tps', {}).get('avg', 0)
                requests = model.get('requests', 0)
                if tps and requests > 0:
                    total_tps_weighted += tps * requests
                    total_tps_count += requests

            avg_tps = total_tps_weighted / total_tps_count if total_tps_count > 0 else 0

            print(f"\n📈 Total Requests: {total_requests}")
            print(f"📈 Avg TPS (all models): {avg_tps:.2f}")

        print("\n💡 Press Ctrl+C to exit")
        print("=" * WIDTH)

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

            # Sort by requests (most active first)
            sorted_models = sorted(self.models, key=lambda m: m.get('requests', 0), reverse=True)

            for model in sorted_models:
                self.draw_model_row(model)

            self.draw_footer(self.models)

    def watch(self, interval: float = 1.0):
        """Continuously watch for changes and update the dashboard."""
        try:
            while True:
                self.render()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n\n👋 Dashboard stopped.")
            sys.exit(0)


def main():
    # Determine summary path from environment or default
    perf_logs_dir = os.getenv("PERFORMANCE_LOGS_DIR", "./__performance_logs")
    summary_path = os.path.join(perf_logs_dir, "summary.yaml")

    if len(sys.argv) > 1:
        summary_path = sys.argv[1]

    dashboard = PerformanceDashboard(summary_path)

    print(f"🔍 Watching: {summary_path}")
    print("💡 Press Ctrl+C to exit\n")

    dashboard.watch(interval=1.0)


if __name__ == "__main__":
    main()
