#!/usr/bin/env python3
"""Performance benchmarking tool for KeepRoLLMing orchestrator.

This script tests all routes with predefined prompts and measures:
- Response time (latency)
- Tokens per second throughput (prompt_tps, completion_tps, total_tps)
- Total tokens processed
- Success/failure rates

Usage:
    python benchmark_routes.py --config config.yaml --output benchmarks/
"""

import argparse
import asyncio
import json
import os
import re
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
        self.backend_model: str = ""  # Store the actual backend model name
        self.success: bool = False
        self.error_message: Optional[str] = None

        # Timing metrics (in milliseconds)
        self.elapsed_ms: float = 0.0
        self.ttft_ms: float = 0.0

        # Token metrics
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.total_tokens: int = 0

        # Throughput metrics (tokens per second) - matching performance module format
        self.prompt_tps: float = 0.0      # Prompt processing throughput (prompt / ttft)
        self.completion_tps: float = 0.0  # Generation throughput (completion / generation_time)
        self.total_tps: float = 0.0       # Overall throughput ((prompt + completion) / elapsed)


class BenchmarkRunner:
    """Runs benchmarks across all routes."""

    def __init__(self, config_path: str, timeout: int = 60, verbose: bool = False):
        self.config_path = config_path
        self.timeout = timeout
        self.verbose = verbose

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
        backend_model: str,  # The actual model to send to backend
    ) -> BenchmarkResult:
        """Test a single route with the given prompt using streaming."""
        result = BenchmarkResult()
        result.route_name = route_name
        result.upstream_url = upstream_url
        result.backend_model = backend_model  # Store backend model name

        # Build request payload - use backend_model (actual model name) not route name
        payload = {
            "model": backend_model,  # Use resolved backend model
            "messages": [{"role": "user", "content": prompt_content}],
            "max_tokens": 256,
            "temperature": 0.7,
            "stream": True,
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=2.0, read=self.timeout, write=self.timeout, pool=self.timeout)) as client:
                # Measure total latency and time to first token
                start_time = time.time()
                ttft_start = None  # Track when we receive the first chunk
                
                prompt_tokens = 0
                completion_tokens = 0

                async with client.stream(
                    "POST",
                    f"{upstream_url}/v1/chat/completions",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                ) as response:
                    # Check status code
                    if response.status_code != 200:
                        result.success = False
                        error_text = await response.text()[:500]
                        result.error_message = f"HTTP {response.status_code}: {error_text}"
                        return result

                    # Stream and parse responses
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or line == "data: [DONE]":
                            continue
                        
                        if line.startswith("data:"):
                            # Track TTFT on first chunk
                            if ttft_start is None:
                                ttft_start = time.time()
                            
                            try:
                                data = json.loads(line[5:].strip())
                                
                                # Extract usage metrics from final chunk (Ollama includes usage in last chunk)
                                if "usage" in data:
                                    prompt_tokens = data["usage"].get("prompt_tokens", 0)
                                    completion_tokens = data["usage"].get("completion_tokens", 0)
                                
                                # Check for error in response
                                if "error" in data:
                                    result.success = False
                                    result.error_message = data["error"].get("message", str(data["error"]))
                                    return result
                                    
                            except json.JSONDecodeError:
                                continue

                end_time = time.time()
                result.elapsed_ms = (end_time - start_time) * 1000
                
                # Calculate TTFT if we received any chunks
                if ttft_start is not None:
                    result.ttft_ms = (ttft_start - start_time) * 1000
                
                result.success = True
                result.prompt_tokens = prompt_tokens
                result.completion_tokens = completion_tokens
                result.total_tokens = prompt_tokens + completion_tokens

                # Calculate TPS metrics matching performance module format
                if result.total_tokens > 0 and result.elapsed_ms > 0:
                    # total_tps: overall throughput (prompt + completion) / elapsed
                    result.total_tps = result.total_tokens / (result.elapsed_ms / 1000.0)

                # Calculate prompt_tps and completion_tps if we have TTFT and token counts
                if result.ttft_ms > 0 and result.prompt_tokens > 0:
                    # prompt_tps: prompt tokens / ttft
                    result.prompt_tps = result.prompt_tokens / (result.ttft_ms / 1000.0)

                if result.completion_tokens > 0 and result.elapsed_ms > result.ttft_ms:
                    # completion_tps: completion tokens / generation_time (elapsed - ttft)
                    gen_time = result.elapsed_ms - result.ttft_ms
                    if gen_time > 0:
                        result.completion_tps = result.completion_tokens / (gen_time / 1000.0)

                # If we didn't get token counts from streaming, fall back to non-streaming request
                if prompt_tokens == 0 and completion_tokens == 0:
                    if self.verbose:
                        print(f"    Warning: No usage metrics in streaming response, falling back to non-streaming")
                    
                    # Retry with non-streaming to get token counts
                    payload["stream"] = False
                    try:
                        response = await client.post(
                            f"{upstream_url}/v1/chat/completions",
                            json=payload,
                            headers={"Content-Type": "application/json"},
                        )
                        
                        if response.status_code == 200 and "usage" in response.json():
                            usage = response.json()["usage"]
                            result.prompt_tokens = usage.get("prompt_tokens", 0)
                            result.completion_tokens = usage.get("completion_tokens", 0)
                            result.total_tokens = result.prompt_tokens + result.completion_tokens
                            
                            # Recalculate TPS with actual token counts
                            if result.total_tokens > 0 and result.elapsed_ms > 0:
                                result.total_tps = result.total_tokens / (result.elapsed_ms / 1000.0)

                            if result.ttft_ms > 0 and result.prompt_tokens > 0:
                                result.prompt_tps = result.prompt_tokens / (result.ttft_ms / 1000.0)

                            if result.completion_tokens > 0 and result.elapsed_ms > result.ttft_ms:
                                gen_time = result.elapsed_ms - result.ttft_ms
                                if gen_time > 0:
                                    result.completion_tps = result.completion_tokens / (gen_time / 1000.0)
                    except Exception as fallback_error:
                        if self.verbose:
                            print(f"    Warning: Fallback also failed: {fallback_error}")

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
                backend_model=str(backend_model),  # Ensure it's a string
            )
            result.prompt_name = prompt_name
            results.append(result)
            # Also store in instance for summary printing
            self.results.append(result)

        return results

    def print_summary(self):
        """Print summary statistics grouped by model."""
        successful = [r for r in self.results if r.success]
        failed = [r for r in self.results if not r.success]

        # Group results by backend_model
        models = {}
        for result in successful:
            key = result.backend_model or result.route_name  # Use backend_model if available
            if key not in models:
                models[key] = []
            models[key].append(result)

        print("\n" + "=" * 70)
        print("BENCHMARK SUMMARY")
        print("=" * 70)

        print(f"\nTotal routes tested: {len(self.results)}")
        print(f"Successful: {len(successful)}")
        print(f"Failed: {len(failed)}")

        # Per-model breakdown
        if models:
            print("\n" + "-" * 70)
            print("PER-MODEL BREAKDOWN")
            print("-" * 70)
            
            for model_name, results in sorted(models.items()):
                print(f"\nModel: {model_name}")
                print(f"  Requests: {len(results)}")
                
                avg_latency = sum(r.elapsed_ms for r in results) / len(results)
                avg_prompt_tps = sum(r.prompt_tps for r in results if r.prompt_tps > 0) / len([r for r in results if r.prompt_tps > 0]) if any(r.prompt_tps > 0 for r in results) else 0
                avg_completion_tps = sum(r.completion_tps for r in results if r.completion_tps > 0) / len([r for r in results if r.completion_tps > 0]) if any(r.completion_tps > 0 for r in results) else 0
                avg_total_tps = sum(r.total_tps for r in results) / len(results)

                print(f"  Avg latency: {avg_latency:.2f} ms")
                print(f"  Avg prompt TPS (prompt/TTFT): {avg_prompt_tps:.2f}")
                print(f"  Avg completion TPS (completion/generation_time): {avg_completion_tps:.2f}")
                print(f"  Avg total TPS ((prompt+completion)/elapsed): {avg_total_tps:.2f}")

        # Overall summary across all models
        if successful:
            avg_latency = sum(r.elapsed_ms for r in successful) / len(successful)
            
            # Calculate overall averages matching performance module format
            avg_prompt_tps = sum(r.prompt_tps for r in successful if r.prompt_tps > 0) / len([r for r in successful if r.prompt_tps > 0]) if any(r.prompt_tps > 0 for r in successful) else 0
            avg_completion_tps = sum(r.completion_tps for r in successful if r.completion_tps > 0) / len([r for r in successful if r.completion_tps > 0]) if any(r.completion_tps > 0 for r in successful) else 0
            avg_total_tps = sum(r.total_tps for r in successful) / len(successful)

            print("\n" + "-" * 70)
            print("OVERALL AVERAGES (all models)")
            print("-" * 70)
            print(f"\nAverage latency: {avg_latency:.2f} ms")
            print(f"Average prompt TPS (prompt/TTFT): {avg_prompt_tps:.2f}")
            print(f"Average completion TPS (completion/generation_time): {avg_completion_tps:.2f}")
            print(f"Average total TPS ((prompt+completion)/elapsed): {avg_total_tps:.2f}")

        if failed:
            print("\nFailed routes:")
            for result in failed:
                print(f"  ✗ {result.route_name}: {result.error_message}")


def save_results(results: List[BenchmarkResult], output_dir: str):
    """Save benchmark results to JSON files."""
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Group results by backend_model for per-model files
    models = {}
    for result in results:
        key = result.backend_model or result.route_name
        if key not in models:
            models[key] = []
        models[key].append(result)

    # Save individual route results
    for result in results:
        filename = f"{result.route_name.replace('/', '_')}_benchmark.json"
        filepath = Path(output_dir) / filename

        data = {
            "route_name": result.route_name,
            "backend_model": result.backend_model,
            "upstream_url": result.upstream_url if result.upstream_url else None,
            "prompt_name": result.prompt_name,
            "success": result.success,
            "error_message": result.error_message,
            "elapsed_ms": float(result.elapsed_ms),
            "ttft_ms": float(result.ttft_ms) if result.ttft_ms else 0.0,
            "prompt_tokens": int(result.prompt_tokens) if result.prompt_tokens else 0,
            "completion_tokens": int(result.completion_tokens) if result.completion_tokens else 0,
            "total_tokens": int(result.total_tokens) if result.total_tokens else 0,
            "prompt_tps": float(result.prompt_tps) if result.prompt_tps > 0 else 0.0,
            "completion_tps": float(result.completion_tps) if result.completion_tps > 0 else 0.0,
            "total_tps": float(result.total_tps) if result.total_tps > 0 else 0.0,
        }

        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

    # Save per-model aggregated results
    for model_name, model_results in models.items():
        successful = [r for r in model_results if r.success]
        failed = [r for r in model_results if not r.success]
        
        model_aggregated = {
            "timestamp": timestamp,
            "model": model_name,
            "total_routes": len(model_results),
            "successful": len(successful),
            "failed": len(failed),
            "results": [
                {
                    "route_name": r.route_name,
                    "prompt_name": r.prompt_name,
                    "success": r.success,
                    "elapsed_ms": float(r.elapsed_ms) if r.elapsed_ms else 0.0,
                    "ttft_ms": float(r.ttft_ms) if r.ttft_ms else 0.0,
                    "prompt_tps": float(r.prompt_tps) if r.prompt_tps > 0 else 0.0,
                    "completion_tps": float(r.completion_tps) if r.completion_tps > 0 else 0.0,
                    "total_tps": float(r.total_tps) if r.total_tps > 0 else 0.0,
                    "error_message": r.error_message,
                }
                for r in model_results
            ],
        }

        model_file = Path(output_dir) / f"benchmark_{model_name.replace('/', '_')}_aggregated_{timestamp}.json"
        with open(model_file, 'w') as f:
            json.dump(model_aggregated, f, indent=2)

    # Save overall aggregated results (all models combined)
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
                "backend_model": r.backend_model,
                "prompt_name": r.prompt_name,
                "success": r.success,
                "elapsed_ms": float(r.elapsed_ms) if r.elapsed_ms else 0.0,
                "ttft_ms": float(r.ttft_ms) if r.ttft_ms else 0.0,
                "prompt_tps": float(r.prompt_tps) if r.prompt_tps > 0 else 0.0,
                "completion_tps": float(r.completion_tps) if r.completion_tps > 0 else 0.0,
                "total_tps": float(r.total_tps) if r.total_tps > 0 else 0.0,
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
    parser.add_argument(
        "--filter", "-f",
        type=str,
        default=None,
        help="Filter routes by name pattern (e.g., 'arkai/' to match only arkai/* routes)",
    )
    parser.add_argument(
        "--num-prompts", "-n",
        type=int,
        default=3,
        help="Number of prompts to run (default: 3)",
    )

    args = parser.parse_args()

    print(f"Running benchmark on configuration: {args.config}")
    print("=" * 70)

    runner = BenchmarkRunner(args.config, timeout=args.timeout, verbose=args.verbose)

    # Filter routes by name pattern if specified
    if args.filter:
        pattern = re.compile(args.filter)
        original_count = len(runner.routes)
        runner.routes = [r for r in runner.routes if pattern.search(r.name)]
        print(f"Filtering routes with pattern '{args.filter}': {original_count} -> {len(runner.routes)} matches")

    all_results = []

    # Select prompts to run
    prompts_to_run = TEST_PROMPTS[:args.num_prompts]

    for prompt in prompts_to_run:
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
