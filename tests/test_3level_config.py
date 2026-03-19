#!/usr/bin/env python3

"""
Comprehensive tests for 3-level configuration hierarchy resolution.
Tests defaults → models → routes priority order.
"""

import pytest
from keeprollming.routing import Route, DefaultSettings, ModelConfig, _UNSET


class TestThreeLevelHierarchy:
    """Test the 3-level configuration hierarchy resolution."""

    def test_defaults_only(self):
        """When no model or route overrides, use defaults."""
        from keeprollming.config import resolve_route_settings
        
        route = Route(
            name="test",
            pattern="test/*",
            main_model="unknown-model",  # Not in models config
            ctx_len=_UNSET,  # type: ignore
            max_tokens=_UNSET,  # type: ignore
        )
        
        defaults = DefaultSettings(ctx_len=8192, max_tokens=4096)
        models_config = {}
        
        ctx_len, max_tokens = resolve_route_settings(route, models_config, defaults)
        
        assert ctx_len == 8192
        assert max_tokens == 4096

    def test_model_override_defaults(self):
        """Model config overrides defaults."""
        from keeprollming.config import resolve_route_settings
        
        route = Route(
            name="test",
            pattern="test/*",
            main_model="qwen3.5-35b-a3b@q3_k_s",
            ctx_len=_UNSET,  # type: ignore
            max_tokens=_UNSET,  # type: ignore
        )
        
        defaults = DefaultSettings(ctx_len=8192, max_tokens=4096)
        models_config = {
            "qwen3.5-35b-a3b@q3_k_s": ModelConfig(
                ctx_len=128000,
                max_tokens=32768,
            )
        }
        
        ctx_len, max_tokens = resolve_route_settings(route, models_config, defaults)
        
        assert ctx_len == 128000
        assert max_tokens == 32768

    def test_route_override_model(self):
        """Route config overrides model config."""
        from keeprollming.config import resolve_route_settings
        
        route = Route(
            name="test",
            pattern="test/*",
            main_model="qwen3.5-35b-a3b@q3_k_s",
            ctx_len=16384,  # Override model default
            max_tokens=_UNSET,  # type: ignore
        )
        
        defaults = DefaultSettings(ctx_len=8192, max_tokens=4096)
        models_config = {
            "qwen3.5-35b-a3b@q3_k_s": ModelConfig(
                ctx_len=128000,  # Should be overridden by route
                max_tokens=32768,
            )
        }
        
        ctx_len, max_tokens = resolve_route_settings(route, models_config, defaults)
        
        assert ctx_len == 16384  # Route override wins
        assert max_tokens == 32768  # Model config used

    def test_full_hierarchy_chain(self):
        """Test complete hierarchy: defaults → model → route."""
        from keeprollming.config import resolve_route_settings
        
        route = Route(
            name="test",
            pattern="test/*",
            main_model="qwen3.5-35b-a3b@q3_k_s",
            ctx_len=64000,  # Override both model and defaults
            max_tokens=8192,  # Override both model and defaults
        )
        
        defaults = DefaultSettings(ctx_len=8192, max_tokens=4096)
        models_config = {
            "qwen3.5-35b-a3b@q3_k_s": ModelConfig(
                ctx_len=128000,  # Should be overridden by route
                max_tokens=32768,  # Should be overridden by route
            )
        }
        
        ctx_len, max_tokens = resolve_route_settings(route, models_config, defaults)
        
        assert ctx_len == 64000  # Route wins
        assert max_tokens == 8192  # Route wins

    def test_partial_model_override(self):
        """Model only overrides some fields."""
        from keeprollming.config import resolve_route_settings
        
        route = Route(
            name="test",
            pattern="test/*",
            main_model="qwen3.5-35b-a3b@q3_k_s",
            ctx_len=_UNSET,  # type: ignore
            max_tokens=_UNSET,  # type: ignore
        )
        
        defaults = DefaultSettings(ctx_len=8192, max_tokens=4096)
        models_config = {
            "qwen3.5-35b-a3b@q3_k_s": ModelConfig(
                ctx_len=128000,  # Only override ctx_len
                # max_tokens not set, should fall back to defaults
            )
        }
        
        ctx_len, max_tokens = resolve_route_settings(route, models_config, defaults)
        
        assert ctx_len == 128000  # From model config
        assert max_tokens == 4096  # From defaults (model didn't override)

    def test_model_not_found_falls_back_to_defaults(self):
        """When model not in models_config, fall back to defaults."""
        from keeprollming.config import resolve_route_settings
        
        route = Route(
            name="test",
            pattern="test/*",
            main_model="unknown-model",  # Not in config
            ctx_len=_UNSET,  # type: ignore
            max_tokens=_UNSET,  # type: ignore
        )
        
        defaults = DefaultSettings(ctx_len=8192, max_tokens=4096)
        models_config = {
            "known-model": ModelConfig(
                ctx_len=65536,
                max_tokens=8192,
            )
        }
        
        ctx_len, max_tokens = resolve_route_settings(route, models_config, defaults)
        
        assert ctx_len == 8192  # From defaults
        assert max_tokens == 4096  # From defaults

    def test_route_override_with_unknown_model(self):
        """Route overrides even when model not in config."""
        from keeprollming.config import resolve_route_settings
        
        route = Route(
            name="test",
            pattern="test/*",
            main_model="unknown-model",  # Not in models_config
            ctx_len=4096,  # Override defaults directly
            max_tokens=_UNSET,  # type: ignore
        )
        
        defaults = DefaultSettings(ctx_len=8192, max_tokens=4096)
        models_config = {}
        
        ctx_len, max_tokens = resolve_route_settings(route, models_config, defaults)
        
        assert ctx_len == 4096  # Route override wins over defaults
        assert max_tokens == 4096  # From defaults

    def test_summary_enabled_inheritance(self):
        """Test that summary_enabled also follows hierarchy."""
        from keeprollming.config import resolve_route_settings
        
        route = Route(
            name="test",
            pattern="test/*",
            main_model="qwen3.5-35b-a3b@q3_k_s",
            ctx_len=_UNSET,  # type: ignore
            max_tokens=_UNSET,  # type: ignore
        )
        
        defaults = DefaultSettings(ctx_len=8192, max_tokens=4096, summary_enabled=True)
        models_config = {
            "qwen3.5-35b-a3b@q3_k_s": ModelConfig(
                ctx_len=128000,
                max_tokens=32768,
                summary_enabled=False,  # Override defaults
            )
        }
        
        ctx_len, max_tokens = resolve_route_settings(route, models_config, defaults)
        
        assert ctx_len == 128000
        assert max_tokens == 32768


class TestRealWorldScenarios:
    """Test real-world configuration scenarios."""

    def test_chat_quick_scenario(self):
        """chat/quick uses qwen2.5-3b-instruct with model defaults."""
        from keeprollming.config import resolve_route_settings
        
        route = Route(
            name="chat/quick",
            pattern="chat/quick|c/q",
            main_model="qwen2.5-3b-instruct",
            ctx_len=_UNSET,  # type: ignore
            max_tokens=_UNSET,  # type: ignore
        )
        
        defaults = DefaultSettings(ctx_len=8192, max_tokens=4096)
        models_config = {
            "qwen2.5-3b-instruct": ModelConfig(
                ctx_len=8192,
                max_tokens=4096,
            )
        }
        
        ctx_len, max_tokens = resolve_route_settings(route, models_config, defaults)
        
        assert ctx_len == 8192
        assert max_tokens == 4096

    def test_chat_deep_scenario(self):
        """chat/deep uses qwen3.5-35b-a3b with large context."""
        from keeprollming.config import resolve_route_settings
        
        route = Route(
            name="chat/deep",
            pattern="chat/deep|c/d",
            main_model="qwen3.5-35b-a3b@q3_k_s",
            ctx_len=_UNSET,  # type: ignore
            max_tokens=_UNSET,  # type: ignore
        )
        
        defaults = DefaultSettings(ctx_len=8192, max_tokens=4096)
        models_config = {
            "qwen3.5-35b-a3b@q3_k_s": ModelConfig(
                ctx_len=128000,
                max_tokens=32768,
            )
        }
        
        ctx_len, max_tokens = resolve_route_settings(route, models_config, defaults)
        
        assert ctx_len == 128000
        assert max_tokens == 32768

    def test_sys_memory_scenario(self):
        """sys/memory overrides model defaults with custom values."""
        from keeprollming.config import resolve_route_settings
        
        route = Route(
            name="sys/memory",
            pattern="sys/memory|s/mem",
            main_model="qwen3.5-35b-a3b@q3_k_s",
            ctx_len=64000,  # Override model default (128000)
            max_tokens=8192,  # Override model default (32768)
        )
        
        defaults = DefaultSettings(ctx_len=8192, max_tokens=4096)
        models_config = {
            "qwen3.5-35b-a3b@q3_k_s": ModelConfig(
                ctx_len=128000,
                max_tokens=32768,
            )
        }
        
        ctx_len, max_tokens = resolve_route_settings(route, models_config, defaults)
        
        assert ctx_len == 64000  # Route override wins
        assert max_tokens == 8192  # Route override wins

    def test_code_senior_scenario(self):
        """code/senior uses focused context for code review."""
        from keeprollming.config import resolve_route_settings
        
        route = Route(
            name="code/senior",
            pattern="code/senior|c/sn",
            main_model="qwen3.5-35b-a3b@q3_k_s",
            ctx_len=16384,  # Override model default for focused review
            max_tokens=_UNSET,  # type: ignore
        )
        
        defaults = DefaultSettings(ctx_len=8192, max_tokens=4096)
        models_config = {
            "qwen3.5-35b-a3b@q3_k_s": ModelConfig(
                ctx_len=128000,
                max_tokens=32768,
            )
        }
        
        ctx_len, max_tokens = resolve_route_settings(route, models_config, defaults)
        
        assert ctx_len == 16384  # Route override wins
        assert max_tokens == 32768  # Model config used


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
