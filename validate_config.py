#!/usr/bin/env python3
"""CLI tool for validating and health-checking KeepRoLLMing configuration.

Usage examples:
    # Validate config structure only
    python validate_config.py --config config.yaml validate

    # Run live health check
    python validate_config.py --config config.yaml healthcheck

    # Full validation + health check
    python validate_config.py --config config.yaml --full-check

    # Verbose output
    python validate_config.py --config config.yaml --verbose
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from keeprollming.config import CONFIG, load_user_routes
from keeprollming.healthcheck import HealthCheckResults, run_health_check
from keeprollming.routing import BUILTIN_ROUTES, Route
from keeprollming.validator import (
    ValidationWarning,
    ValidationResult,
    check_circular_references,
    print_validation_report,
    validate_config,
)


def load_config(config_path: str) -> dict:
    """Load configuration from a YAML file."""
    path = Path(config_path)
    
    if not path.exists():
        print(f"Error: Config file not found: {config_path}")
        sys.exit(1)
    
    try:
        with open(path, "r") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"Error: Failed to parse YAML config: {e}")
        sys.exit(1)


def build_routes_by_name(config: dict) -> dict[str, Route]:
    """Build dictionary of all routes by name."""
    user_routes = load_user_routes(config)
    all_routes = user_routes + BUILTIN_ROUTES
    return {route.name: route for route in all_routes}


def cmd_validate(args):
    """Run configuration validation."""
    config = load_config(args.config)
    routes_by_name = build_routes_by_name(config)
    
    print(f"Validating configuration: {args.config}")
    print("-" * 60)
    
    result = validate_config(config, routes_by_name)
    print_validation_report(result)
    
    return 0 if result.is_valid else 1


def cmd_healthcheck(args):
    """Run live health check on configured routes."""
    config = load_config(args.config)
    
    print(f"Running health check on configuration: {args.config}")
    print("-" * 60)
    print(f"Timeout: {args.timeout}s, Max concurrent: {args.max_concurrent}\n")
    
    try:
        results = run_health_check(
            config,
            timeout=args.timeout,
            max_concurrent=args.max_concurrent,
            verbose=args.verbose,
        )
        results.print_report()
        
        return 0 if results.is_healthy else 1
        
    except Exception as e:
        print(f"Error during health check: {e}")
        import traceback
        traceback.print_exc()
        return 1


def cmd_full_check(args):
    """Run both validation and health check."""
    config = load_config(args.config)
    routes_by_name = build_routes_by_name(config)
    
    print(f"Running full check on configuration: {args.config}")
    print("=" * 60)
    
    # Phase 1: Validate structure
    print("\n[Phase 1/2] Validating configuration structure...")
    print("-" * 60)
    
    struct_result = validate_config(config, routes_by_name)
    print_validation_report(struct_result)
    
    if not struct_result.is_valid:
        print("\n⚠ Configuration has validation errors. Skipping health check.")
        return 1
    
    # Phase 2: Health check
    print("\n[Phase 2/2] Running live health checks...")
    print("-" * 60)
    
    try:
        health_results = run_health_check(
            config,
            timeout=args.timeout,
            max_concurrent=args.max_concurrent,
            verbose=args.verbose,
        )
        health_results.print_report()
        
        print("\n" + "=" * 60)
        print("FULL CHECK SUMMARY")
        print("=" * 60)
        print(f"Structure validation: ✓ PASSED")
        print(f"Health check: {'✓ PASSED' if health_results.is_healthy else '✗ FAILED'}")
        
        return 0 if health_results.is_healthy else 1
        
    except Exception as e:
        print(f"\nError during health check: {e}")
        import traceback
        traceback.print_exc()
        return 1


def main():
    parser = argparse.ArgumentParser(
        description="Validate and health-check KeepRoLLMing configuration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --config config.yaml validate
  %(prog)s --config config.yaml healthcheck --timeout 5
  %(prog)s --config config.yaml --full-check --verbose
        """,
    )
    
    parser.add_argument(
        "--config", "-c",
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--timeout", "-t",
        type=int,
        default=10,
        help="Timeout in seconds for health checks (default: 10)",
    )
    parser.add_argument(
        "--max-concurrent", "-m",
        type=int,
        default=5,
        help="Maximum concurrent health check requests (default: 5)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output",
    )
    
    # Create shared argument group for subparsers
    common_args = argparse.ArgumentParser(add_help=False)
    common_args.add_argument(
        "--config", "-c",
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)",
    )
    common_args.add_argument(
        "--timeout", "-t",
        type=int,
        default=10,
        help="Timeout in seconds for health checks (default: 10)",
    )
    common_args.add_argument(
        "--max-concurrent", "-m",
        type=int,
        default=5,
        help="Maximum concurrent health check requests (default: 5)",
    )
    common_args.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # Validate command
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate configuration structure and inheritance",
        parents=[common_args],
    )
    validate_parser.set_defaults(func=cmd_validate)
    
    # Health check command
    health_parser = subparsers.add_parser(
        "healthcheck",
        help="Run live health checks on configured routes",
        parents=[common_args],
    )
    health_parser.set_defaults(func=cmd_healthcheck)
    
    # Full check command (no subcommand)
    full_parser = subparsers.add_parser(
        "full-check",
        help="Run both validation and health check",
        parents=[common_args],
    )
    full_parser.set_defaults(func=cmd_full_check)

    args = parser.parse_args()
    
    # If no command specified, run full check
    if args.command is None:
        args.func = cmd_full_check
    
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
