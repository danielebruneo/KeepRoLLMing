#!/usr/bin/env python3

"""
Unit tests for the new route-based configuration system.
Tests pattern matching, fallback chains, and route resolution.
"""

import pytest
from keeprollming.routing import (
    Route,
    DEFAULT_FALLBACK_ROUTE,
    _parse_pattern,
    _match_route,
    resolve_route,
    resolve_fallback_chain,
    get_route_settings,
)


class TestPatternParsing:
    """Test pattern parsing logic."""

    def test_exact_match_pattern(self):
        """Test exact match patterns like 'builtin/quick'."""
        regex, is_wildcard = _parse_pattern("builtin/quick")
        assert not is_wildcard
        assert regex.match("builtin/quick") is not None
        assert regex.match("builtin/main") is None

    def test_wildcard_pattern(self):
        """Test wildcard patterns like 'pass/*'."""
        regex, is_wildcard = _parse_pattern("pass/*")
        assert is_wildcard
        assert regex.match("pass/openai/gpt-4") is not None
        assert regex.match("pass/anthropic/claude") is not None
        assert regex.match("local/quick") is None

    def test_multiple_patterns(self):
        """Test multiple patterns separated by |."""
        regex, is_wildcard = _parse_pattern("builtin/quick|quick-fallback")
        assert regex.match("builtin/quick") is not None
        assert regex.match("quick-fallback") is not None
        assert regex.match("main") is None

    def test_code_patterns(self):
        """Test code/senior and code/junior patterns."""
        regex, _ = _parse_pattern("builtin/code/senior|senior-fallback")
        assert regex.match("builtin/code/senior") is not None
        assert regex.match("senior-fallback") is not None

        regex2, _ = _parse_pattern("builtin/code/junior|junior-fallback")
        assert regex2.match("builtin/code/junior") is not None
        assert regex2.match("junior-fallback") is not None


class TestRouteResolution:
    """Test route resolution logic."""

    def test_resolve_fallback(self):
        """Test fallback for unmatched model."""
        route, backend = resolve_route("completely/unknown/model")
        assert route is not None  # Falls back to DEFAULT_FALLBACK_ROUTE

    def test_resolve_fallback_for_unknown(self):
        """Test fallback for unknown model."""
        route, backend = resolve_route("unknown-model")
        assert route is not None
        assert "fallback" in route.name.lower()


class TestFallbackChain:
    """Test fallback chain resolution."""

    def test_simple_fallback_chain(self):
        """Test simple fallback chain with model names."""
        route = Route(
            name="test-route",
            pattern="deep",
            model="qwen2.5-27b-instruct",
            fallback_chain=[
                "qwen2.5-14b-instruct",
                "local/quick",
            ],
        )

        attempts = resolve_fallback_chain(route, "qwen2.5-27b-instruct")
        
        assert len(attempts) == 3  # primary + 2 fallbacks
        assert attempts[0] == (route, "qwen2.5-27b-instruct")
        assert attempts[1][1] == "qwen2.5-14b-instruct"
        assert attempts[2][1] == "local/quick"

    @pytest.mark.skip(reason="Route name resolution requires populated USER_ROUTES — deferred to integration tests")
    def test_fallback_chain_with_route_reference(self):
        """Test fallback chain referencing other routes."""
        route = Route(
            name="deep-route",
            pattern="deep",
            model="qwen3.5-35b-a3b",
            fallback_chain=[
                "api/gpt-4",  # Reference to built-in api prefix route
            ],
        )

        attempts = resolve_fallback_chain(route, "qwen3.5-35b-a3b")
        
        assert len(attempts) == 2
        assert attempts[0] == (route, "qwen3.5-35b-a3b")
        # Second attempt should reference the matched route
        assert "passthrough" in attempts[1][0].name

    def test_fallback_chain_max_depth(self):
        """Test that fallback chain respects max depth."""
        route = Route(
            name="test-route",
            pattern="deep",
            model="qwen2.5-27b-instruct",
            fallback_chain=[
                "model1",
                "model2",
                "model3",
                "model4",  # Should be cut off at depth 3
            ],
        )

        attempts = resolve_fallback_chain(route, "qwen2.5-27b-instruct")
        
        assert len(attempts) == 4  # primary + 3 fallbacks (max depth)

    def test_fallback_chain_prevents_loops(self):
        """Test that fallback chain prevents infinite loops."""
        route = Route(
            name="test-route",
            pattern="deep",
            model="qwen2.5-27b-instruct",
            fallback_chain=[
                "qwen2.5-14b-instruct",
                "qwen2.5-27b-instruct",  # Same as primary - should be skipped
                "local/quick",
            ],
        )

        attempts = resolve_fallback_chain(route, "qwen2.5-27b-instruct")
        
        # Should skip the duplicate model
        assert len(attempts) == 3  # primary + 14b + quick (not 27b again)


class TestRouteSettings:
    """Test route settings extraction."""

    def test_get_route_settings_basic(self):
        """Test basic route settings extraction."""
        route = Route(
            name="test-route",
            pattern="local/quick",
            model="qwen2.5-3b-instruct",
            summary_model="qwen2.5-1.5b-instruct",
            ctx_len=8192,
        )

        settings = get_route_settings(route, "qwen2.5-3b-instruct")

        assert settings.route_name == "test-route"
        assert settings.upstream_model == "qwen2.5-3b-instruct"
        assert settings.summary_model == "qwen2.5-1.5b-instruct"
        assert settings.ctx_len == 8192

    def test_get_route_settings_passthrough(self):
        """Test route settings for passthrough routes."""
        route = Route(
            name="passthrough-route",
            pattern="pass/*",
            passthrough_enabled=True,
            summary_enabled=False,
        )

        settings = get_route_settings(route, "openai/gpt-4")

        assert settings.passthrough_enabled is True
        assert settings.summary_enabled is False


class TestRouteInheritance:
    """Test route inheritance functionality."""

    def test_summary_enabled_inheritance(self):
        """Test that summary_enabled is properly inherited from parent routes."""
        from keeprollming.routing import resolve_inherited_route

        # Create a parent route with summary_enabled=False
        parent = Route(
            name="parent-route",
            pattern="parent/*",
            summary_enabled=False,
            model="qwen2.5-3b-instruct",
        )

        # Create a child route that extends parent but doesn't specify summary_enabled
        child = Route(
            name="child-route",
            pattern="child/*",
            extends=["parent-route"],
            model="qwen2.5-7b-instruct",
        )

        routes_by_name = {"parent-route": parent, "child-route": child}

        # Resolve the child route - it should inherit summary_enabled=False from parent
        resolved_child = resolve_inherited_route(child, routes_by_name)

        assert resolved_child.summary_enabled is False, (
            "Child should inherit summary_enabled=False from parent"
        )

    def test_summary_enabled_override(self):
        """Test that child can override summary_enabled from parent."""
        from keeprollming.routing import resolve_inherited_route

        # Create a parent route with summary_enabled=True
        parent = Route(
            name="parent-route",
            pattern="parent/*",
            summary_enabled=True,
            model="qwen2.5-3b-instruct",
        )

        # Create a child route that overrides summary_enabled=False
        child = Route(
            name="child-route",
            pattern="child/*",
            extends=["parent-route"],
            summary_enabled=False,  # Override parent
            model="qwen2.5-7b-instruct",
        )

        routes_by_name = {"parent-route": parent, "child-route": child}

        # Resolve the child route - should use child's value
        resolved_child = resolve_inherited_route(child, routes_by_name)

        assert resolved_child.summary_enabled is False, (
            "Child should use its own summary_enabled=False value"
        )

    def test_passthrough_enabled_inheritance(self):
        """Test that passthrough_enabled is properly inherited from parent routes."""
        from keeprollming.routing import resolve_inherited_route

        # Create a parent route with passthrough_enabled=True
        parent = Route(
            name="parent-route",
            pattern="parent/*",
            passthrough_enabled=True,
            model="qwen2.5-3b-instruct",
        )

        # Create a child route that extends parent but doesn't specify passthrough_enabled
        child = Route(
            name="child-route",
            pattern="child/*",
            extends=["parent-route"],
            model="qwen2.5-7b-instruct",
        )

        routes_by_name = {"parent-route": parent, "child-route": child}

        # Resolve the child route - it should inherit passthrough_enabled=True from parent
        resolved_child = resolve_inherited_route(child, routes_by_name)

        assert resolved_child.passthrough_enabled is True, (
            "Child should inherit passthrough_enabled=True from parent"
        )

    def test_summary_model_not_route_object(self):
        """Test that summary_model is always a string, never a Route object."""
        from keeprollming.routing import get_route_settings

        # Create a route with a valid summary_model string
        route = Route(
            name="test-route",
            pattern="test/*",
            model="qwen2.5-3b-instruct",
            summary_model="qwen2.5-1.5b-instruct",
        )

        settings = get_route_settings(route, "qwen2.5-3b-instruct")

        # summary_model should be a string, not an object
        assert isinstance(settings.summary_model, str), (
            "summary_model should be a string"
        )
        assert settings.summary_model == "qwen2.5-1.5b-instruct"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

class TestSummaryModelDefault:
    """Test summary_model defaulting behavior."""

    def test_summary_model_defaults_to_model_when_none(self):
        """Test that summary_model defaults to model when not explicitly set."""
        from keeprollming.routing import get_route_settings

        # Create a route without explicit summary_model (defaults to None)
        route = Route(
            name="test-route",
            pattern="test/*",
            model="qwen2.5-3b-instruct",
            summary_enabled=True,
        )

        settings = get_route_settings(route, "qwen2.5-3b-instruct")

        # summary_model should default to the main model
        assert isinstance(settings.summary_model, str), (
            "summary_model should be a string"
        )
        assert settings.summary_model == "qwen2.5-3b-instruct", (
            "summary_model should default to model when not explicitly set"
        )

    def test_summary_model_inheritance_with_default(self):
        """Test that summary_model defaults to model after inheritance resolution."""
        from keeprollming.routing import resolve_inherited_route, get_route_settings

        # Parent route with model defined but no summary_model
        parent = Route(
            name="parent",
            pattern="parent",
            model="qwen3.5-4b",
            summary_enabled=True,
        )

        # Child route inherits from parent without setting summary_model
        child = Route(
            name="child",
            pattern="child",
            extends="parent",
            summary_enabled=False,
        )

        routes_by_name = {"parent": parent, "child": child}
        resolved = resolve_inherited_route(child, routes_by_name)

        settings = get_route_settings(resolved, "qwen3.5-4b")

        # summary_model should default to the main model after inheritance
        assert isinstance(settings.summary_model, str), (
            "summary_model should be a string"
        )
        assert settings.summary_model == "qwen3.5-4b", (
            "summary_model should default to model after inheritance"
        )

    def test_summary_model_override_in_child(self):
        """Test that child can override summary_model from parent."""
        from keeprollming.routing import resolve_inherited_route, get_route_settings

        # Parent route with explicit summary_model
        parent = Route(
            name="parent",
            pattern="parent",
            model="qwen3.5-4b",
            summary_model="qwen2.5-3b-instruct",
            summary_enabled=True,
        )

        # Child route overrides summary_model
        child = Route(
            name="child",
            pattern="child",
            extends="parent",
            summary_model="qwen2.5-1.5b-instruct",  # Override with smaller model
            summary_enabled=True,
        )

        routes_by_name = {"parent": parent, "child": child}
        resolved = resolve_inherited_route(child, routes_by_name)

        settings = get_route_settings(resolved, "qwen3.5-4b")

        # Child should use its own summary_model override
        assert settings.summary_model == "qwen2.5-1.5b-instruct", (
            "Child should use overridden summary_model"
        )
