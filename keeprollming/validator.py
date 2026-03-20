"""Configuration validation for KeepRoLLMing orchestrator.

This module provides tools to validate configuration files for structural correctness,
inheritance chain validity, and completeness of required fields.

Usage:
    from keeprollming.validator import validate_config
    
    errors = validate_config(config_dict)
    if errors:
        print("Validation failed:")
        for error in errors:
            print(f"  - {error}")
    else:
        print("Configuration is valid!")
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional, Set, Tuple

from keeprollming.config import CONFIG
from keeprollming.routing import BUILTIN_ROUTES, Route, _UNSET, resolve_inherited_route


class ValidationError(Exception):
    """Raised when configuration validation fails."""
    pass


class ValidationWarning:
    """Represents a non-critical warning during validation."""
    
    def __init__(self, message: str, route_name: Optional[str] = None, field: Optional[str] = None):
        self.message = message
        self.route_name = route_name
        self.field = field
    
    def __str__(self) -> str:
        parts = [self.message]
        if self.route_name:
            parts.append(f" (route: {self.route_name})")
        if self.field:
            parts.append(f" (field: {self.field})")
        return "".join(parts)


class ValidationResult:
    """Container for validation results."""
    
    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[ValidationWarning] = []
        self.valid_routes: List[Tuple[str, Route]] = []
        self.invalid_routes: List[Tuple[str, str]] = []  # (route_name, error_message)
    
    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0 and len(self.invalid_routes) == 0
    
    def add_error(self, message: str, route_name: Optional[str] = None, field: Optional[str] = None):
        parts = [message]
        if route_name:
            parts.append(f" (route: {route_name})")
        if field:
            parts.append(f" (field: {field})")
        self.errors.append("".join(parts))
    
    def add_warning(self, warning: ValidationWarning):
        self.warnings.append(warning)
    
    def add_valid_route(self, route_name: str, route: Route):
        self.valid_routes.append((route_name, route))
    
    def add_invalid_route(self, route_name: str, error_message: str):
        self.invalid_routes.append((route_name, error_message))
    
    def print_report(self):
        """Print a human-readable validation report."""
        if self.is_valid:
            print("✓ Configuration is valid!")
            print(f"  Valid routes: {len(self.valid_routes)}")
            if self.warnings:
                print(f"  Warnings: {len(self.warnings)}")
                for warning in self.warnings:
                    print(f"    ⚠ {warning}")
        else:
            print("✗ Configuration validation failed!")
            print(f"\nErrors ({len(self.errors)}):")
            for error in self.errors:
                print(f"  ✗ {error}")
            
            print(f"\nInvalid routes ({len(self.invalid_routes)}):")
            for route_name, error in self.invalid_routes:
                print(f"  ✗ {route_name}: {error}")
            
            if self.warnings:
                print(f"\nWarnings ({len(self.warnings)}):")
                for warning in self.warnings:
                    print(f"  ⚠ {warning}")


def validate_config(config: Dict[str, Any], routes_by_name: Dict[str, Route]) -> ValidationResult:
    """
    Validate a configuration dictionary.
    
    Args:
        config: The configuration dictionary
        routes_by_name: Dictionary of all routes by name (user + builtin)
        
    Returns:
        ValidationResult object with errors and warnings
    """
    result = ValidationResult()
    
    # Check for circular references in inheritance chains
    check_circular_references(routes_by_name, result)
    
    # Validate each route's inheritance chain
    validate_inheritance_chains(routes_by_name, result)
    
    # Validate non-private routes have all required fields
    validate_required_fields(config, routes_by_name, result)
    
    return result


def check_circular_references(
    routes_by_name: Dict[str, Route], 
    result: ValidationResult
) -> Optional[List[str]]:
    """
    Check for circular references in route inheritance chains.
    
    Args:
        routes_by_name: Dictionary of all routes by name
        result: ValidationResult to add errors to
        
    Returns:
        List of route names in the cycle if found, None otherwise
    """
    visited = set()
    rec_stack = set()
    
    def dfs(route_name: str, path: List[str]) -> Optional[List[str]]:
        visited.add(route_name)
        rec_stack.add(route_name)
        path.append(route_name)
        
        route = routes_by_name.get(route_name)
        if not route:
            return None
        
        # Handle both single extends and list of extends
        extends_list = []
        if isinstance(route.extends, str):
            extends_list = [route.extends]
        elif isinstance(route.extends, list):
            extends_list = route.extends
        
        for parent_name in extends_list:
            if parent_name not in routes_by_name:
                continue  # Will be caught in inheritance validation
            
            if parent_name not in visited:
                cycle = dfs(parent_name, path.copy())
                if cycle:
                    return cycle
            elif parent_name in rec_stack:
                # Found a cycle
                cycle_start = path.index(parent_name) if parent_name in path else len(path)
                return path[cycle_start:] + [parent_name]
        
        rec_stack.remove(route_name)
        return None
    
    for route_name in routes_by_name:
        if route_name not in visited:
            cycle = dfs(route_name, [])
            if cycle:
                result.add_error(
                    f"Circular inheritance detected: {' -> '.join(cycle)}",
                    route_name=cycle[0] if cycle else None
                )
                return cycle
    
    return None


def validate_inheritance_chains(
    routes_by_name: Dict[str, Route], 
    result: ValidationResult
):
    """
    Validate that all inheritance chains can be resolved.
    
    Args:
        routes_by_name: Dictionary of all routes by name
        result: ValidationResult to add errors/warnings to
    """
    for route_name, route in routes_by_name.items():
        # Skip builtin routes for this check (they're always valid)
        if route_name.startswith("builtin/"):
            continue
        
        # Try to resolve the inheritance chain
        try:
            resolved = resolve_inherited_route(route, routes_by_name.copy())
            result.add_valid_route(route_name, resolved)
        except KeyError as e:
            missing_parent = str(e)
            result.add_invalid_route(
                route_name,
                f"Cannot resolve inheritance: parent route '{missing_parent}' not found"
            )
        except Exception as e:
            result.add_invalid_route(
                route_name,
                f"Inheritance resolution failed: {str(e)}"
            )


def validate_required_fields(
    config: Dict[str, Any], 
    routes_by_name: Dict[str, Route], 
    result: ValidationResult
):
    """
    Validate that non-private routes have all required fields.
    
    Args:
        config: The configuration dictionary
        routes_by_name: Dictionary of all resolved routes by name
        result: ValidationResult to add errors/warnings to
    """
    # Required fields for a route to be functional
    required_fields = ["upstream_url"]
    
    for route_name, route in routes_by_name.items():
        # Skip builtin routes (they're fallbacks)
        if route_name.startswith("builtin/"):
            continue
        
        # Skip private routes (they're for organization only)
        if getattr(route, "_is_private", False):
            continue
        
        # Resolve inheritance to get final values
        try:
            resolved = resolve_inherited_route(route, routes_by_name)
        except Exception:
            continue  # Already reported in validate_inheritance_chains
        
        # Check required fields on resolved route
        for field in required_fields:
            value = getattr(resolved, field, _UNSET)
            if value is _UNSET or value is None:
                result.add_error(
                    f"Missing required field '{field}'",
                    route_name=route_name,
                    field=field
                )


def print_validation_report(result: ValidationResult):
    """Print a formatted validation report to stdout."""
    if result.is_valid:
        print("✓ Configuration is valid!")
        print(f"\nValid routes ({len(result.valid_routes)}):")
        
        for route_name, route in result.valid_routes:  # Show ALL routes
            upstream = getattr(route, "upstream_url", None)
            main_model = getattr(route, "main_model", None)
            
            # Format values nicely - check against _UNSET sentinel
            upstream_str = "N/A" if upstream is _UNSET or not upstream else str(upstream)
            model_str = "N/A" if main_model is _UNSET or not main_model else str(main_model)
            
            print(f"  • {route_name}")
            print(f"    → upstream: {upstream_str}")
            print(f"    → model: {model_str}")

        if result.warnings:
            print(f"\n⚠ Warnings ({len(result.warnings)}):")
            for warning in result.warnings:
                print(f"  • {warning}")
    else:
        print("✗ Configuration validation failed!")
        print(f"\nErrors ({len(result.errors)}):")
        for error in result.errors:
            print(f"  ✗ {error}")
        
        print(f"\nInvalid routes ({len(result.invalid_routes)}):")
        for route_name, error in result.invalid_routes[:10]:
            print(f"  ✗ {route_name}: {error}")
        if len(result.invalid_routes) > 10:
            print(f"  ... and {len(result.invalid_routes) - 10} more routes")
        
        if result.warnings:
            print(f"\n⚠ Warnings ({len(result.warnings)}):")
            for warning in result.warnings:
                print(f"  • {warning}")


# Convenience function for quick validation
def quick_validate(config_file: Optional[str] = None) -> bool:
    """
    Quick validation of configuration.
    
    Args:
        config_file: Path to config file (uses default if None)
        
    Returns:
        True if valid, False otherwise
    """
    from keeprollming.config import load_user_routes
    
    try:
        user_routes = load_user_routes(CONFIG)
        all_routes = user_routes + BUILTIN_ROUTES
        routes_by_name = {route.name: route for route in all_routes}
        
        result = validate_config(CONFIG, routes_by_name)
        print_validation_report(result)
        
        return result.is_valid
    except Exception as e:
        print(f"✗ Validation failed with exception: {e}")
        return False
