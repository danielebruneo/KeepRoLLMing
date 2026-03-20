#!/usr/bin/env python3
"""Performance benchmarking tool for KeepRoLLMing orchestrator.

This script tests all routes with predefined prompts and measures:
- Response time (latency)
- Tokens per second throughput
- Total tokens processed
- Success/failure rates

Usage:
    python benchmark_routes.py --config config.yaml --output benchmarks/
"""

import argparse
import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

import httpx
import yaml

from keeprollming.config import load_user_routes, CONFIG
from keeprollming.routing import BUILTIN_ROUTES, _UNSET


# Predefined test prompts (medium size ~200 tokens)
TEST_PROMPTS = [
    {
        "name": "story",
        "description": "Write a 150-word story about a robot learning to paint",
        "content": """You are an expert storyteller. Write a compelling short story (approximately 150 words) 
about a curious robot named Nova who discovers the art of painting in an abandoned human art studio. 
Nova has never seen colors before and must learn to understand them through touch, sound, and eventually sight.
Include sensory details about how Nova experiences color for the first time - describe the feeling of red like warm sunlight,
blue like cool water, green like fresh leaves. Show Nova's emotional journey from confusion to wonder as it creates 
its first painting. End with a reflective moment about what art means to something that was built without emotions but 
learns to feel through creation.""",
    },
    {
        "name": "technical_explanation",
        "description": "Explain quantum entanglement in simple terms",
        "content": """You are a science educator explaining complex concepts to high school students. 
Write a clear, engaging explanation of quantum entanglement (approximately 150 words). Start with a relatable analogy 
using everyday objects like paired gloves or dice rolls. Then explain what makes quantum entanglement special and different 
from classical physics - the idea that particles can be connected in ways that seem to defy our normal understanding of space 
and distance. Include one real-world application or experiment that demonstrates this phenomenon (like Einstein's EPR paradox 
or Bell's theorem). Keep the tone friendly and accessible, avoiding overly technical jargon while still being scientifically accurate.""",
    },
    {
        "name": "code_review",
        "description": "Review a Python function for bugs and improvements",
        "content": """You are an experienced software engineer conducting a code review. Analyze the following Python function 
and provide feedback on potential bugs, performance issues, and best practices violations (approximately 150 words):

def process_data(items, threshold=0):
    result = []
    for item in items:
        if item > threshold:
            result.append(item * 2)
        else:
            pass
    return result

Consider edge cases like empty lists, None values, negative numbers, and floating point precision. 
Also suggest improvements for code readability and efficiency. Provide specific examples of how to fix any issues you identify.""",
    },
]


class BenchmarkResult:
    """Stores results from a single benchmark run."""

    def __init__(self):
        self.route_name: str = ""
        self.upstream_url: str = ""
        self.prompt_name: str = ""
        self.success: bool = False
        self.error_message: Optional[str] = None

        # Timing metrics (in milliseconds)
        self.total_latency_ms: float = 0.0
        self.time_to_first_token_ms: float = 0.0
        
        # Phase timing (for separate TPS calculations)
        self.prompt_processing_time_ms: float = 0.0  # Time until first token
        self.generation_time_ms: float = 0.0  # Time from first token to completion

        # Token metrics
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.total_tokens: int = 0

        # Throughput metrics (tokens per second)
        self.prompt_tps: float = 0.0      # Prompt processing throughput
        self.completion_tps: float = 0.0  # Generation throughput
        self.tps: float = 0.0             # Overall throughput (prompt + completion combined)


class BenchmarkRunner:
    """Runs benchmarks across all routes."""
    
    def __init__(self, config_path: str, timeout: int = 60):
        self.config_path = config_path
        self.timeout = timeout
        
        # Load configuration
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        # Build routes list (non-private only for benchmarking)
        user_routes = load_user_routes(self.config)
        all_routes = user_routes + BUILTIN_ROUTES
        self.routes = [r for r in all_routes if not getattr(r, '_is_private', False)]
        
        # Results storage
        self.results: List[BenchmarkResult] = []
    
    async def test_route(
        self, 
        route_name: str, 
        upstream_url: str, 
        prompt_content: str,
        prompt_tokens: int,
        backend_model: str,  # The actual model to send to backend
    ) -> BenchmarkResult:
        """Test a single route with the given prompt."""
        result = BenchmarkResult()
        result.route_name = route_name
        result.upstream_url = upstream_url
        
        # Build request payload - use backend_model (actual model name) not route name
        payload = {
            "model": backend_model,  # Use resolved backend model
            "messages": [{"role": "user", "content": prompt_content}],
            "max_tokens": 256,
            "temperature": 0.7,
        }
        
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=2.0, read=self.timeout, write=self.timeout, pool=self.timeout)) as client:
                # Measure total latency and time to first token
                start_time = time.time()
                
                # Use regular POST request instead of streaming context manager
                response = await client.post(
                    f"{upstream_url}/v1/chat/completions",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                
                end_time = time.time()
                result.total_latency_ms = (end_time - start_time) * 1000
                
                # Check status code
                if response.status_code != 200:
                    result.success = False
                    error_text = await response.text()[:500]
                    result.error_message = f"HTTP {response.status_code}: {error_text}"
                    return result
                
                # Parse response (non-streaming)
                data = response.json()
                
                # Extract usage metrics
                if "usage" in data:
                    result.prompt_tokens = data["usage"].get("prompt_tokens", 0)
                    result.completion_tokens = data["usage"].get("completion_tokens", 0)
                    result.total_tokens = data["usage"].get("total_tokens", 0)
                
                # Check for error in response
                if "error" in data:
                    result.success = False
                    result.error_message = data["error"].get("message", str(data["error"]))
                    return result
                
                # For non-streaming, we can't accurately measure TTFT
                # So we'll estimate based on total latency and token counts
                if result.total_tokens > 0:
                    result.tps = (result.total_tokens / result.total_latency_ms) * 1000 if result.total_latency_ms > 0 else 0
                
                # Estimate prompt_tps and completion_tps as rough approximations
                if result.prompt_tokens > 0 and result.completion_tokens > 0:
                    # Assume roughly equal time for prompt processing and generation
                    estimated_ttft = result.total_latency_ms * 0.3
                    result.time_to_first_token_ms = estimated_ttft
                    result.prompt_processing_time_ms = estimated_ttft
                    result.generation_time_ms = result.total_latency_ms - estimated_ttft
                    
                    if result.prompt_processing_time_ms > 0:
                        result.prompt_tps = (result.prompt_tokens / result.prompt_processing_time_ms) * 1000
                    if result.generation_time_ms > 0:
                        result.completion_tps = (result.completion_tokens / result.generation_time_ms) * 1000

        except httpx.TimeoutException as e:
            result.success = False
            result.error_message = f"Request timed out after {self.timeout}s"
        except httpx.ConnectError as e:
            result.success = False
            result.error_message = f"Connection failed: {str(e)}"
        except Exception as e:
            result.success = False
            result.error_message = f"Unexpected error: {str(e)}"

        return result
    
    async def run_benchmark(
        self, 
        prompt_name: str, 
        prompt_content: str,
        verbose: bool = True,
    ) -> List[BenchmarkResult]:
        """Run benchmark on all routes with the given prompt."""
        results = []

        # Build complete routes dictionary including private routes for inheritance resolution
        from keeprollming.config import load_user_routes
        user_routes = load_user_routes(self.config)
        all_routes_for_resolution = user_routes + BUILTIN_ROUTES
        routes_by_name_full = {r.name: r for r in all_routes_for_resolution}

        for route in self.routes:
            # Skip builtin fallbacks
            if route.name.startswith("builtin/"):
                continue

            # Get upstream URL and backend model (resolve inheritance using full routes dict)
            from keeprollming.routing import resolve_inherited_route, get_route_settings

            resolved = resolve_inherited_route(route, routes_by_name_full)

            # Use resolved.main_model directly as the backend model
            backend_model = resolved.main_model if hasattr(resolved, 'main_model') else None

            # Get settings for upstream_url
            settings = get_route_settings(resolved, str(backend_model) if backend_model and backend_model is not _UNSET else "unknown")

            upstream_url = settings.get("upstream_url")

            # Skip if no upstream URL or backend model
            if not upstream_url:
                if verbose:
                    print(f"  Skipping {route.name}: No upstream URL configured")
                continue

            if not backend_model or backend_model is _UNSET:
                if verbose:
                    print(f"  Skipping {route.name}: No backend model resolved")
                continue

            if verbose:
                print(f"  Testing {route.name} (model: {backend_model})...")

            result = await self.test_route(
                route_name=route.name,
                upstream_url=upstream_url,
                prompt_content=prompt_content,
                prompt_tokens=len(prompt_content.split()),
                backend_model=str(backend_model),  # Ensure it's a string
            )
            results.append(result)
            # Also store in instance for summary printing
            self.results.append(result)

        return results
    
    def print_summary(self):
        """Print summary statistics."""
        successful = [r for r in self.results if r.success]
        failed = [r for r in self.results if not r.success]

        print("\n" + "=" * 70)
        print("BENCHMARK SUMMARY")
        print("=" * 70)

        print(f"\nTotal routes tested: {len(self.results)}")
        print(f"Successful: {len(successful)}")
        print(f"Failed: {len(failed)}")

        if successful:
            avg_latency = sum(r.total_latency_ms for r in successful) / len(successful)
            avg_prompt_tps = sum(r.prompt_tps for r in successful if r.prompt_tps > 0) / len([r for r in successful if r.prompt_tps > 0]) if any(r.prompt_tps > 0 for r in successful) else 0
            avg_completion_tps = sum(r.completion_tps for r in successful if r.completion_tps > 0) / len([r for r in successful if r.completion_tps > 0]) if any(r.completion_tps > 0 for r in successful) else 0
            avg_tps = sum(r.tps for r in successful) / len(successful)

            print(f"\nAverage latency: {avg_latency:.2f} ms")
            print(f"Average prompt processing TPS: {avg_prompt_tps:.2f}")
            print(f"Average completion TPS: {avg_completion_tps:.2f}")
            print(f"Average overall TPS: {avg_tps:.2f}")

        if failed:
            print("\nFailed routes:")
            for result in failed:
                print(f"  ✗ {result.route_name}: {result.error_message}")


def save_results(results: List[BenchmarkResult], output_dir: str):
    """Save benchmark results to JSON files."""
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save individual route results
    for result in results:
        filename = f"{result.route_name.replace('/', '_')}_benchmark.json"
        filepath = Path(output_dir) / filename

        data = {
            "route_name": result.route_name,
            "upstream_url": result.upstream_url if result.upstream_url else None,
            "success": result.success,
            "error_message": result.error_message,
            "total_latency_ms": float(result.total_latency_ms),
            "time_to_first_token_ms": float(result.time_to_first_token_ms) if result.time_to_first_token_ms else 0.0,
            "prompt_processing_time_ms": float(result.prompt_processing_time_ms) if result.prompt_processing_time_ms else 0.0,
            "generation_time_ms": float(result.generation_time_ms) if result.generation_time_ms else 0.0,
            "prompt_tokens": int(result.prompt_tokens) if result.prompt_tokens else 0,
            "completion_tokens": int(result.completion_tokens) if result.completion_tokens else 0,
            "total_tokens": int(result.total_tokens) if result.total_tokens else 0,
            "prompt_tps": float(result.prompt_tps) if result.prompt_tps > 0 else 0.0,
            "completion_tps": float(result.completion_tps) if result.completion_tps > 0 else 0.0,
            "tps": float(result.tps) if result.tps > 0 else 0.0,
        }

        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

    # Save aggregated results
    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    aggregated = {
        "timestamp": timestamp,
        "total_routes": len(results),
        "successful": len(successful),
        "failed": len(failed),
        "results": [
            {
                "route_name": r.route_name,
                "success": r.success,
                "total_latency_ms": float(r.total_latency_ms) if r.total_latency_ms else 0.0,
                "time_to_first_token_ms": float(r.time_to_first_token_ms) if r.time_to_first_token_ms else 0.0,
                "prompt_tps": float(r.prompt_tps) if r.prompt_tps > 0 else 0.0,
                "completion_tps": float(r.completion_tps) if r.completion_tps > 0 else 0.0,
                "tps": float(r.tps) if r.tps > 0 else 0.0,
                "error_message": r.error_message,
            }
            for r in results
        ],
    }

    aggregated_file = Path(output_dir) / f"benchmark_aggregated_{timestamp}.json"
    with open(aggregated_file, 'w') as f:
        json.dump(aggregated, f, indent=2)


async def main():
    parser = argparse.ArgumentParser(description="Benchmark KeepRoLLMing routes")
    parser.add_argument(
        "--config", "-c",
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--output", "-o",
        default="benchmarks",
        help="Output directory for results (default: benchmarks)",
    )
    parser.add_argument(
        "--timeout", "-t",
        type=int,
        default=60,
        help="Request timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output",
    )

    args = parser.parse_args()

    print(f"Running benchmark on configuration: {args.config}")
    print("=" * 70)

    runner = BenchmarkRunner(args.config, timeout=args.timeout)

    all_results = []

    for prompt in TEST_PROMPTS:
        print(f"\nPrompt: {prompt['name']} - {prompt['description']}")
        print("-" * 70)

        results = await runner.run_benchmark(
            prompt_name=prompt["name"],
            prompt_content=prompt["content"],
            verbose=args.verbose,
        )
        all_results.extend(results)

    # Print summary
    runner.print_summary()

    # Save results
    save_results(all_results, args.output)
    print(f"\nResults saved to: {args.output}/")


if __name__ == "__main__":
    asyncio.run(main())
