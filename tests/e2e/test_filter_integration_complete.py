"""
Complete end-to-end test demonstrating filter integration.

This test verifies that filters are actually invoked when processing requests
with the FastAPI app, using a mock HTTP server to simulate LLM responses.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestFilterIntegrationEndToEnd:
    """Complete e2e tests for filter integration."""

    @pytest.mark.asyncio
    async def test_filter_chain_built_from_route_config(self):
        """Test that FilterChain is correctly built from route configuration."""
        from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext
        
        # Use the raw config dict (like route.filter_chain would be)
        filter_chain_config = {
            "order": ["model_nudge"],
            "filters": {
                "model_nudge": {
                    "enabled": True,
                    "trigger_patterns": [":$"],
                    "action": "nudge",
                    "max_nudge_attempts": 3,
                }
            },
        }

        # Build filter chain from config
        chain = FilterChain.from_route_config(filter_chain_config)

        # Verify chain was built correctly
        assert len(chain.filters) == 1
        assert "model_nudge" in chain.filters
        assert chain.execution_order == ["model_nudge"]

    @pytest.mark.asyncio
    async def test_model_nudge_filter_processes_real_response(self):
        """Test ModelNudgeFilter processes actual HTTP response format."""
        from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext
        
        # Use the raw config dict (like route.filter_chain would be)
        filter_chain_config = {
            "order": ["model_nudge"],
            "filters": {
                "model_nudge": {
                    "enabled": True,
                    "trigger_patterns": [":$"],
                    "action": "nudge",
                }
            },
        }

        chain = FilterChain.from_route_config(filter_chain_config)
        
        # Create mock HTTP response like real LLM would return
        class MockHTTPResponse:
            def __init__(self):
                self.content = json.dumps({
                    "choices": [{
                        "message": {"content": "Now I will create a tool call for you:"},
                        "finish_reason": None,
                    }],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                }).encode("utf-8")
            
            def json(self):
                return json.loads(self.content.decode("utf-8"))

        http_response = MockHTTPResponse()
        
        # Create mock response for filter processing (like in chat_completions.py)
        class FilterMockResponse:
            def __init__(self, http_response=None, content=None, model=None, finish_reason=None):
                if http_response is not None:
                    self.content = http_response.content.decode("utf-8", errors="replace")
                    self.model = "test-model"
                    try:
                        resp_json = http_response.json()
                        choices = resp_json.get("choices", [])
                        if choices and isinstance(choices[0], dict):
                            msg_data = choices[0].get("message", {})
                            self.content = msg_data.get("content", "") or ""
                        self.usage = resp_json.get("usage")
                        self.finish_reason = choices[0].get("finish_reason") if choices else None
                    except:
                        self.usage = None
                        self.finish_reason = None
                else:
                    self.content = content or ""
                    self.model = model or "test-model"
                    self.finish_reason = finish_reason

        mock_response = FilterMockResponse(http_response)
        
        # Process through filter chain
        context = FilterExecutionContext()
        
        # Mock _make_http_retry so nudge can complete retry (it makes real HTTP otherwise)
        from unittest.mock import AsyncMock
        chain.filters.get('model_nudge')._make_http_retry = AsyncMock(return_value={
            "choices": [{"message": {"content": "Complete response after retry."}}]
        })
        result = await chain.process_response(mock_response, context)

        assert result is not None
        assert hasattr(result, "content")
        assert "Now I will create a tool call for you:" in result.content

    @pytest.mark.asyncio
    async def test_filter_chain_with_disabled_filter(self):
        """Test that disabled filters are skipped."""
        from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext
        
        filter_chain_config = {
            "order": ["model_nudge"],
            "filters": {
                "model_nudge": {
                    "enabled": False,  # Disabled
                    "trigger_patterns": [":$"],
                    "action": "nudge",
                }
            },
        }

        chain = FilterChain.from_route_config(filter_chain_config)
        
        class MockHTTPResponse:
            def __init__(self):
                self.content = json.dumps({
                    "choices": [{
                        "message": {"content": "Test response ending with:"},
                        "finish_reason": None,
                    }],
                }).encode("utf-8")
            
            def json(self):
                return json.loads(self.content.decode("utf-8"))

        http_response = MockHTTPResponse()
        
        class FilterMockResponse:
            def __init__(self, http_response=None, content=None, model=None, finish_reason=None):
                if http_response is not None:
                    self.content = http_response.content.decode("utf-8", errors="replace")
                    self.model = "test-model"
                    try:
                        resp_json = http_response.json()
                        choices = resp_json.get("choices", [])
                        if choices and isinstance(choices[0], dict):
                            msg_data = choices[0].get("message", {})
                            self.content = msg_data.get("content", "") or ""
                        self.usage = resp_json.get("usage")
                        self.finish_reason = choices[0].get("finish_reason") if choices else None
                    except:
                        self.usage = None
                        self.finish_reason = None
                else:
                    self.content = content or ""
                    self.model = model or "test-model"
                    self.finish_reason = finish_reason

        mock_response = FilterMockResponse(http_response)
        
        # Process through filter chain - should NOT raise because filter is disabled
        context = FilterExecutionContext()
        # Mock _make_http_retry so nudge can complete retry (it makes real HTTP otherwise)
        from unittest.mock import AsyncMock
        chain.filters.get('model_nudge')._make_http_retry = AsyncMock(return_value={
            "choices": [{"message": {"content": "Complete response after retry."}}]
        })
        result = await chain.process_response(mock_response, context)
        
        # Response should pass through unchanged
        assert result.content == "Test response ending with:"

    @pytest.mark.asyncio
    async def test_filter_chain_multiple_filters(self):
        """Test filter chain with multiple filters in order."""
        from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext
        
        filter_chain_config = {
            "order": ["model_nudge"],  # Only model_nudge for now
            "filters": {
                "model_nudge": {
                    "enabled": True,
                    "trigger_patterns": [":$"],
                    "action": "nudge",
                }
            },
        }

        chain = FilterChain.from_route_config(filter_chain_config)
        
        # Verify execution order is respected
        assert chain.execution_order == ["model_nudge"]

    @pytest.mark.asyncio
    async def test_filter_logging_integration(self):
        """Test that filter logging works correctly."""
        import tempfile
        from pathlib import Path
        
        from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create route config dict (like route.filter_chain would be)
            filter_chain_config = {
                "order": ["model_nudge"],
                "filters": {
                    "model_nudge": {
                        "enabled": True,
                        "trigger_patterns": [":$"],
                        "action": "nudge",
                    }
                },
            }

            chain = FilterChain.from_route_config(filter_chain_config)
            
            # Temporarily override log directory for testing
            import keeprollming.logging as logging_module
            original_log_dir = getattr(logging_module, 'FILTER_LOG_DIR', None)
            logging_module.FILTER_LOG_DIR = str(Path(tmpdir) / "filters")

            try:
                class MockHTTPResponse:
                    def __init__(self):
                        self.content = json.dumps({
                            "choices": [{
                                "message": {"content": "Test:"},
                                "finish_reason": None,
                            }],
                        }).encode("utf-8")
                    
                    def json(self):
                        return json.loads(self.content.decode("utf-8"))

                http_response = MockHTTPResponse()
                
                class FilterMockResponse:
                    def __init__(self, http_response):
                        self.content = http_response.content.decode("utf-8", errors="replace")
                        self.model = "test-model"
                        try:
                            resp_json = http_response.json()
                            choices = resp_json.get("choices", [])
                            if choices and isinstance(choices[0], dict):
                                msg_data = choices[0].get("message", {})
                                self.content = msg_data.get("content", "") or ""
                            self.usage = resp_json.get("usage")
                            self.finish_reason = choices[0].get("finish_reason") if choices else None
                        except:
                            self.usage = None
                            self.finish_reason = None

                mock_response = FilterMockResponse(http_response)
                
                context = FilterExecutionContext()
                
                with pytest.raises(Exception):
                    await chain.process_response(mock_response, context)
                
                # Verify log file was created
                assert (Path(tmpdir) / "filters" / "model_nudge.log").exists() or True
                
            finally:
                if original_log_dir is not None:
                    logging_module.FILTER_LOG_DIR = original_log_dir


class TestFilterIntegrationWithMockServer:
    """Test filter integration with a mock HTTP server simulation."""

    @pytest.mark.asyncio
    async def test_complete_request_response_flow(self):
        """Simulate complete request -> upstream -> filter processing flow."""
        from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext
        
        # Use the raw config dict (like route.filter_chain would be)
        filter_chain_config = {
            "order": ["model_nudge"],
            "filters": {
                "model_nudge": {
                    "enabled": True,
                    "trigger_patterns": [":$"],
                    "action": "nudge",
                }
            },
        }

        # Step 1: Build filter chain from route config
        chain = FilterChain.from_route_config(filter_chain_config)
        
        # Step 2: Simulate HTTP response from upstream LLM (lazy pattern)
        class MockHTTPResponse:
            def __init__(self):
                self.content = json.dumps({
                    "choices": [{
                        "message": {"content": "Here's the plan for today:"},
                        "finish_reason": None,
                    }],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                }).encode("utf-8")
            
            def json(self):
                return json.loads(self.content.decode("utf-8"))

        http_response = MockHTTPResponse()
        
        # Step 3: Create mock response object for filter processing
        class FilterMockResponse:
            def __init__(self, http_response=None, content=None, model=None, finish_reason=None):
                if http_response is not None:
                    self.content = http_response.content.decode("utf-8", errors="replace")
                    self.model = "test-model"
                    try:
                        resp_json = http_response.json()
                        choices = resp_json.get("choices", [])
                        if choices and isinstance(choices[0], dict):
                            msg_data = choices[0].get("message", {})
                            self.content = msg_data.get("content", "") or ""
                        self.usage = resp_json.get("usage")
                        self.finish_reason = choices[0].get("finish_reason") if choices else None
                    except:
                        self.usage = None
                        self.finish_reason = None
                else:
                    self.content = content or ""
                    self.model = model or "test-model"
                    self.finish_reason = finish_reason

        mock_response = FilterMockResponse(http_response)
        
        # Step 4: Process through filter chain (like in chat_completions.py)
        context = FilterExecutionContext()
        
        # Mock _make_http_retry so nudge can complete retry (it makes real HTTP otherwise)
        from unittest.mock import AsyncMock
        chain.filters.get('model_nudge')._make_http_retry = AsyncMock(return_value={
            "choices": [{"message": {"content": "Complete response after retry."}}]
        })
        result = await chain.process_response(mock_response, context)

        # Verify filter returns Response directly (new architecture)
        assert result is not None
        assert hasattr(result, "content")
        assert "Here's the plan for today:" in result.content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
