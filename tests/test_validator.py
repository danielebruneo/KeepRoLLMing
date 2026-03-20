"""Unit tests for configuration validation logic."""

import pytest
from typing import Any

from keeprollming.routing import Route, _UNSET


# ============================================================================
# Test Data
# ============================================================================

def create_routes(**routes):
    """Helper to create a routes dict for testing."""
    return routes


# ============================================================================
# Tests for resolve_inherited_route
# ============================================================================

def test_resolve_single_parent():
    """Test resolving a route with a single parent."""
    from keeprollming.routing import resolve_inherited_route
    
    routes = create_routes(
        parent=Route(name="parent", pattern="parent", upstream_url="http://parent.com", main_model="model-p"),
        child=Route(name="child", pattern="child", extends="parent", upstream_url=_UNSET, main_model=_UNSET),
    )
    
    resolved = resolve_inherited_route(routes["child"], routes)
    
    assert resolved.upstream_url == "http://parent.com"
    assert resolved.main_model == "model-p"


def test_resolve_child_overrides_parent():
    """Test that child settings override parent settings."""
    from keeprollming.routing import resolve_inherited_route
    
    routes = create_routes(
        parent=Route(name="parent", pattern="parent", upstream_url="http://parent.com", main_model="model-p"),
        child=Route(name="child", pattern="child", extends="parent", upstream_url="http://child.com", main_model=_UNSET),
    )
    
    resolved = resolve_inherited_route(routes["child"], routes)
    
    assert resolved.upstream_url == "http://child.com"  # Child value wins
    assert resolved.main_model == "model-p"  # Inherited from parent


def test_resolve_multiple_extends():
    """Test resolving a route that extends multiple parents (comma-separated)."""
    from keeprollming.routing import resolve_inherited_route
    
    routes = create_routes(
        base=Route(name="base", pattern="base", upstream_url="http://base.com", main_model="model-base"),
        extra=Route(name="extra", pattern="extra", upstream_url=_UNSET, main_model="model-extra"),
        child=Route(name="child", pattern="child", extends="base,extra", upstream_url=_UNSET, main_model=_UNSET),
    )
    
    resolved = resolve_inherited_route(routes["child"], routes)
    
    # Should inherit from base first (left-to-right merging)
    assert resolved.upstream_url == "http://base.com"
    # Base sets main_model first, then extra can't override because child has _UNSET
    # but base already set it. This is left-to-right merge behavior.
    assert resolved.main_model == "model-base"


def test_resolve_circular_reference():
    """Test that circular references are detected and handled."""
    from keeprollming.routing import resolve_inherited_route
    
    routes = create_routes(
        a=Route(name="a", pattern="a", extends="b"),
        b=Route(name="b", pattern="b", extends="a"),
    )
    
    # Should not raise an error, just return the route as-is
    resolved = resolve_inherited_route(routes["a"], routes)
    
    assert resolved.name == "a"


def test_resolve_missing_parent():
    """Test handling of missing parent routes."""
    from keeprollming.routing import resolve_inherited_route
    
    routes = create_routes(
        child=Route(name="child", pattern="child", extends="nonexistent"),
    )
    
    # Should return child as-is with a warning
    resolved = resolve_inherited_route(routes["child"], routes)
    
    assert resolved.name == "child"


def test_resolve_chain_of_three():
    """Test resolving a chain: grandparent -> parent -> child."""
    from keeprollming.routing import resolve_inherited_route
    
    routes = create_routes(
        grandparent=Route(name="grandparent", pattern="grandparent", upstream_url="http://gp.com", main_model="gp-model"),
        parent=Route(name="parent", pattern="parent", extends="grandparent", upstream_url=_UNSET, main_model="p-model"),
        child=Route(name="child", pattern="child", extends="parent", upstream_url=_UNSET, main_model=_UNSET),
    )
    
    resolved = resolve_inherited_route(routes["child"], routes)
    
    # Child inherits from parent, which inherits from grandparent
    assert resolved.upstream_url == "http://gp.com"  # From grandparent
    assert resolved.main_model == "p-model"  # Overridden by parent


def test_resolve_list_extends():
    """Test resolving a route with extends as a list (converted to comma string)."""
    from keeprollming.routing import resolve_inherited_route
    
    routes = create_routes(
        base=Route(name="base", pattern="base", upstream_url="http://base.com"),
        child=Route(name="child", pattern="child", extends=["base"], upstream_url=_UNSET),  # List format
    )
    
    resolved = resolve_inherited_route(routes["child"], routes)
    
    assert resolved.upstream_url == "http://base.com"


# ============================================================================
# Tests for validator functions
# ============================================================================

def test_validate_circular_reference_detection():
    """Test that circular references are detected in validation."""
    from keeprollming.validator import check_circular_references, ValidationResult
    
    routes_by_name = {
        "a": Route(name="a", pattern="a", extends="b"),
        "b": Route(name="b", pattern="b", extends="c"),
        "c": Route(name="c", pattern="c", extends="a"),  # Circular!
    }
    
    result = ValidationResult()
    check_circular_references(routes_by_name, result)
    
    assert len(result.errors) > 0
    assert any("circular" in error.lower() for error in result.errors)


def test_validate_missing_upstream_url():
    """Test detection of missing upstream_url on non-private routes."""
    from keeprollming.validator import validate_required_fields, ValidationResult
    
    routes_by_name = {
        "route1": Route(name="route1", pattern="route1", upstream_url="http://valid.com"),
        "route2": Route(name="route2", pattern="route2", upstream_url=_UNSET),  # Missing!
        "private_route": Route(name="private_route", pattern="private_route", upstream_url=_UNSET, _is_private=True),
    }
    
    config = {"routes": {}}
    result = ValidationResult()
    validate_required_fields(config, routes_by_name, result)
    
    # Should report route2 as missing upstream_url
    assert any("route2" in error and "upstream_url" in error for error in result.errors)
    # Should NOT report private_route
    assert not any("private_route" in error for error in result.errors)


def test_validate_inheritance_chain_resolution():
    """Test that inheritance chain resolution works correctly."""
    from keeprollming.validator import validate_required_fields, ValidationResult
    
    routes_by_name = {
        "base": Route(name="base", pattern="base", upstream_url="http://base.com", main_model="model"),
        "child": Route(name="child", pattern="child", extends="base", upstream_url=_UNSET, main_model=_UNSET),
        "orphan": Route(name="orphan", pattern="orphan", upstream_url=_UNSET, main_model=_UNSET),
    }
    
    config = {"routes": {}}
    result = ValidationResult()
    validate_required_fields(config, routes_by_name, result)
    
    # child should pass because it inherits from base
    assert not any("child" in error for error in result.errors)
    # orphan should fail because it has no parent with upstream_url
    assert any("orphan" in error and "upstream_url" in error for error in result.errors)


# ============================================================================
# Integration tests
# ============================================================================

def test_full_validation_with_real_config():
    """Test validation against actual config.yaml."""
    from keeprollming.config import load_user_routes, CONFIG
    from keeprollming.routing import BUILTIN_ROUTES
    from keeprollming.validator import validate_config, ValidationResult
    
    user_routes = load_user_routes(CONFIG)
    all_routes = user_routes + BUILTIN_ROUTES
    routes_by_name = {route.name: route for route in all_routes}
    
    result = validate_config(CONFIG, routes_by_name)
    
    # At minimum, should complete without crashing and return a valid result
    assert isinstance(result, ValidationResult)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
