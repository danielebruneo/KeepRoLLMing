#!/usr/bin/env python3

"""
Unit tests for the new route-based configuration system.
Tests pattern matching, fallback chains, and route resolution.
"""

import pytest
from keeprollming.routing import (
    Route,
    BUILTIN_ROUTES,
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
        """Test exact match patterns like 'local/quick'."""
        regex, is_wildcard = _parse_pattern("local/quick")
        assert not is_wildcard
        assert regex.match("local/quick") is not None
        assert regex.match("local/main") is None

    def test_wildcard_pattern(self):
        """Test wildcard patterns like 'pass/*'."""
        regex, is_wildcard = _parse_pattern("pass/*")
        assert is_wildcard
        assert regex.match("pass/openai/gpt-4") is not None
        assert regex.match("pass/anthropic/claude") is not None
        assert regex.match("local/quick") is None

    def test_multiple_patterns(self):
        """Test multiple patterns separated by |."""
        regex, is_wildcard = _parse_pattern("local/quick|quick")
        assert regex.match("local/quick") is not None
        assert regex.match("quick") is not None
        assert regex.match("main") is None

    def test_code_patterns(self):
        """Test code/senior and code/junior patterns."""
        regex, _ = _parse_pattern("code/senior|senior")
        assert regex.match("code/senior") is not None
        assert regex.match("senior") is not None

        regex2, _ = _parse_pattern("code/junior|junior")
        assert regex2.match("code/junior") is not None
        assert regex2.match("junior") is not None


class TestBuiltInRoutes:
    """Test built-in default routes."""

    def test_quick_route_exists(self):
        """Verify quick route is defined."""
        quick_routes = [r for r in BUILTIN_ROUTES if "quick" in r.name]
        assert len(quick_routes) > 0
        route = quick_routes[0]
        assert route.summary_enabled is True
        assert route.passthrough_enabled is False

    def test_main_route_exists(self):
        """Verify main route is defined."""
        main_routes = [r for r in BUILTIN_ROUTES if "main" in r.name and "default" not in r.name]
        # Note: We have main-default, so check by pattern
        main_default = [r for r in BUILTIN_ROUTES if r.pattern == "local/main|main"]
        assert len(main_default) > 0

    def test_deep_route_exists(self):
        """Verify deep route is defined."""
        deep_routes = [r for r in BUILTIN_ROUTES if "deep" in r.name]
        assert len(deep_routes) > 0
        route = deep_routes[0]
        assert route.ctx_len == 16384

    def test_code_senior_route_exists(self):
        """Verify code/senior route is defined."""
        senior_routes = [r for r in BUILTIN_ROUTES if "senior" in r.name]
        assert len(senior_routes) > 0
        route = senior_routes[0]
        assert "code/senior" in route.pattern or "senior" in route.pattern

    def test_code_junior_route_exists(self):
        """Verify code/junior route is defined."""
        junior_routes = [r for r in BUILTIN_ROUTES if "junior" in r.name]
        assert len(junior_routes) > 0
        route = junior_routes[0]
        assert "code/junior" in route.pattern or "junior" in route.pattern

    def test_passthrough_route_exists(self):
        """Verify passthrough route is defined."""
        pass_routes = [r for r in BUILTIN_ROUTES if "passthrough" in r.name]
        assert len(pass_routes) > 0
        route = pass_routes[0]
        assert route.passthrough_enabled is True
        assert route.summary_enabled is False


class TestRouteResolution:
    """Test route resolution logic."""

    def test_resolve_quick_route(self):
        """Test resolving quick profile."""
        route, backend = resolve_route("local/quick")
        assert route is not None
        assert "quick" in route.name or "quick" in route.pattern
        assert backend == "qwen2.5-3b-instruct"

    def test_resolve_main_route(self):
        """Test resolving main profile."""
        route, backend = resolve_route("main")
        assert route is not None
        assert "main" in route.name or "main" in route.pattern
        assert backend == "qwen2.5-v1-7b-instruct"

    def test_resolve_deep_route(self):
        """Test resolving deep profile."""
        route, backend = resolve_route("local/deep")
        assert route is not None
        assert "deep" in route.name or "deep" in route.pattern
        assert backend == "qwen2.5-27b-instruct"

    def test_resolve_code_senior(self):
        """Test resolving code/senior."""
        route, backend = resolve_route("code/senior")
        assert route is not None
        assert "senior" in route.name or "senior" in route.pattern
        assert backend == "qwen3.5-35b-a3b"

    def test_resolve_code_junior(self):
        """Test resolving code/junior."""
        route, backend = resolve_route("code/junior")
        assert route is not None
        assert "junior" in route.name or "junior" in route.pattern
        assert backend == "qwen2.5-7b-instruct"

    def test_resolve_passthrough(self):
        """Test resolving passthrough pattern."""
        route, backend = resolve_route("pass/openai/gpt-4")
        assert route is not None
        assert "passthrough" in route.name
        assert route.passthrough_enabled is True
        # Backend should be extracted from the pattern
        assert backend == "openai/gpt-4"

    def test_resolve_fallback_for_unknown(self):
        """Test fallback for unknown model."""
        route, backend = resolve_route("unknown-model")
        assert route is not None
        assert route.name == "fallback-default"


class TestFallbackChain:
    """Test fallback chain resolution."""

    def test_simple_fallback_chain(self):
        """Test simple fallback chain with model names."""
        route = Route(
            name="test-route",
            pattern="deep",
            main_model="qwen2.5-27b-instruct",
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

    def test_fallback_chain_with_route_reference(self):
        """Test fallback chain referencing other routes."""
        route = Route(
            name="deep-route",
            pattern="deep",
            main_model="qwen3.5-35b-a3b",
            fallback_chain=[
                "local/main",  # Reference to built-in route
            ],
        )

        attempts = resolve_fallback_chain(route, "qwen3.5-35b-a3b")
        
        assert len(attempts) == 2
        assert attempts[0] == (route, "qwen3.5-35b-a3b")
        # Second attempt should reference the matched route
        assert "main" in attempts[1][0].name

    def test_fallback_chain_max_depth(self):
        """Test that fallback chain respects max depth."""
        route = Route(
            name="test-route",
            pattern="deep",
            main_model="qwen2.5-27b-instruct",
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
            main_model="qwen2.5-27b-instruct",
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
            main_model="qwen2.5-3b-instruct",
            summary_model="qwen2.5-1.5b-instruct",
            ctx_len=8192,
        )

        settings = get_route_settings(route, "qwen2.5-3b-instruct")

        assert settings["route_name"] == "test-route"
        assert settings["backend_model"] == "qwen2.5-3b-instruct"
        assert settings["main_model"] == "qwen2.5-3b-instruct"
        assert settings["summary_model"] == "qwen2.5-1.5b-instruct"
        assert settings["ctx_len"] == 8192

    def test_get_route_settings_passthrough(self):
        """Test route settings for passthrough routes."""
        route = Route(
            name="passthrough-route",
            pattern="pass/*",
            passthrough_enabled=True,
            summary_enabled=False,
        )

        settings = get_route_settings(route, "openai/gpt-4")

        assert settings["passthrough_enabled"] is True
        assert settings["summary_enabled"] is False


class TestBackwardCompatibility:
    """Test backward compatibility with old profile system."""

    def test_old_profile_aliases_still_work(self):
        """Test that old model aliases still resolve correctly."""
        # These should work via the built-in routes
        for model in ["local/quick", "quick", "local/main", "main", "local/deep", "deep"]:
            route, backend = resolve_route(model)
            assert route is not None
            assert backend is not None

    def test_passthrough_prefix_still_works(self):
        """Test that pass/* prefix still works for passthrough."""
        route, backend = resolve_route("pass/custom-model")
        assert route is not None
        assert "passthrough" in route.name
        assert backend == "custom-model"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
