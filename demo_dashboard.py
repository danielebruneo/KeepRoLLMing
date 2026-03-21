#!/usr/bin/env python3
"""
Quick demo script for the performance dashboard.
Generates sample summary.yaml data and shows how the dashboard updates in real-time.
"""

import os
import sys
import time
import yaml
from pathlib import Path


def generate_sample_summary():
    """Generate a sample summary.yaml file."""
    perf_logs_dir = os.getenv("PERFORMANCE_LOGS_DIR", "./__performance_logs")
    summary_path = Path(perf_logs_dir) / "summary.yaml"
    
    # Create directory if it doesn't exist
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    
    sample_data = {
        "models": [
            {
                "model": "qwen3.5-4B",
                "requests": 15,
                "tps": {"avg": 42.5, "min": 38.2, "max": 47.8},
                "completion_tps": {"avg": 35.2, "min": 30.1, "max": 40.5},
                "prompt_tps": {"avg": 180.5, "min": 150.2, "max": 210.8},
                "completion_tokens": {"avg": 256.0, "min": 200.0, "max": 300.0},
                "prompt_tokens": {"avg": 145.0, "min": 120.0, "max": 180.0},
                "ttft_ms": {"avg": 450.5, "min": 380.2, "max": 520.8},
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            {
                "model": "qwen3.5-35b-a3b@q3_k_s",
                "requests": 8,
                "tps": {"avg": 52.8, "min": 48.5, "max": 57.2},
                "completion_tps": {"avg": 38.6, "min": 35.0, "max": 42.0},
                "prompt_tps": {"avg": 142.7, "min": 130.5, "max": 155.0},
                "completion_tokens": {"avg": 256.0, "min": 200.0, "max": 300.0},
                "prompt_tokens": {"avg": 148.0, "min": 120.0, "max": 180.0},
                "ttft_ms": {"avg": 1200.5, "min": 1000.2, "max": 1400.8},
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        ]
    }
    
    with open(summary_path, 'w') as f:
        yaml.dump(sample_data, f)
    
    print(f"📝 Created sample summary at: {summary_path}")
    return summary_path


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Demo performance dashboard")
    parser.add_argument("--demo", action="store_true", help="Run demo with simulated updates")
    args = parser.parse_args()
    
    if args.demo:
        # Generate initial data
        summary_path = generate_sample_summary()
        
        print("\n🚀 Starting live update demo (press Ctrl+C to stop)\n")
        
        # Simulate 3 rounds of updates
        for i in range(1, 4):
            time.sleep(2)
            
            # Update data
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            updated_data = {
                "models": [
                    {
                        "model": "qwen3.5-4B",
                        "requests": 15 + i * 10,
                        "tps": {"avg": 42.5 + i * 2, "min": 38.2, "max": 47.8},
                        "completion_tps": {"avg": 35.2 + i * 1, "min": 30.1, "max": 40.5},
                        "prompt_tps": {"avg": 180.5 + i * 5, "min": 150.2, "max": 210.8},
                        "completion_tokens": {"avg": 256.0 + i * 10, "min": 200.0, "max": 300.0},
                        "prompt_tokens": {"avg": 145.0 + i * 5, "min": 120.0, "max": 180.0},
                        "ttft_ms": {"avg": 450.5 - i * 30, "min": 380.2, "max": 520.8},
                        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    },
                    {
                        "model": "qwen3.5-35b-a3b@q3_k_s",
                        "requests": 8 + i * 5,
                        "tps": {"avg": 52.8 + i * 1.5, "min": 48.5, "max": 57.2},
                        "completion_tps": {"avg": 38.6 + i * 0.5, "min": 35.0, "max": 42.0},
                        "prompt_tps": {"avg": 142.7 + i * 3, "min": 130.5, "max": 155.0},
                        "completion_tokens": {"avg": 256.0 + i * 8, "min": 200.0, "max": 300.0},
                        "prompt_tokens": {"avg": 148.0 + i * 4, "min": 120.0, "max": 180.0},
                        "ttft_ms": {"avg": 1200.5 - i * 50, "min": 1000.2, "max": 1400.8},
                        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    },
                ]
            }
            
            with open(summary_path, 'w') as f:
                yaml.dump(updated_data, f)
            
            print(f"📊 Update {i}/3 applied")
        
        print("\n✅ Demo complete!")
    else:
        # Just create the sample file
        generate_sample_summary()


if __name__ == "__main__":
    main()
