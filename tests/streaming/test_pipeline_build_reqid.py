"""Test for req_id correlation in pipeline_build event.

Verifies that emit_pipeline_build() is called with non-None req_id when
_build_pipeline_if_configured() is invoked from process_streaming_request().

This test corresponds to Regression 2 from INVESTIGATION-092-RUNTIME-PARITY-AUDIT-001.
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

from keeprollming.endpoints.streaming_handlers import _build_pipeline


# ---------------------------------------------------------------------------
# test_build_pipeline_if_configured_passes_reqid
# ---------------------------------------------------------------------------


def test_build_pipeline_passes_reqid():
    """_build_pipeline() passes req_id to emit_pipeline_build().

    Regression 2 fix: previously req_id=None was hardcoded. Now it accepts
    and passes req_id through for event correlation.
    """
    # Mock the dependencies
    with patch(
        "keeprollming.endpoints.streaming_handlers.PIPELINE_AVAILABLE", True
    ):
        with patch(
            "keeprollming.endpoints.streaming_handlers.Pipeline"
        ) as mock_pipeline:
            with patch(
                "keeprollming.endpoints.streaming_handlers.emit_pipeline_build"
            ) as mock_emit:
                # Setup mocks
                mock_pipeline.from_route_config.return_value = MagicMock()
                route = MagicMock()
                route.filters = {"system_prompt": {}, "timestamp": {}}
                route.name = "test-route"
                route.api_key = None

                test_req_id = "req-12345-test"

                # Call with req_id
                result = _build_pipeline(route, req_id=test_req_id)

                # Verify pipeline was built
                assert result is not None

                # Verify emit_pipeline_build was called with correct req_id
                mock_emit.assert_called_once()
                call_kwargs = mock_emit.call_args.kwargs
                assert call_kwargs["req_id"] == test_req_id, (
                    f"emit_pipeline_build must be called with req_id={test_req_id!r}, "
                    f"got req_id={call_kwargs['req_id']!r}"
                )


# ---------------------------------------------------------------------------
# test_build_pipeline_if_configured_default_none
# ---------------------------------------------------------------------------


def test_build_pipeline_default_none():
    """_build_pipeline() defaults req_id to None when not provided.

    Backward compatibility: callers that don't pass req_id still work.
    """
    with patch(
        "keeprollming.endpoints.streaming_handlers.PIPELINE_AVAILABLE", True
    ):
        with patch(
            "keeprollming.endpoints.streaming_handlers.Pipeline"
        ) as mock_pipeline:
            with patch(
                "keeprollming.endpoints.streaming_handlers.emit_pipeline_build"
            ) as mock_emit:
                mock_pipeline.from_route_config.return_value = MagicMock()
                route = MagicMock()
                route.filters = {"system_prompt": {}}
                route.name = "test-route"
                route.api_key = None

                # Call without req_id (backward compatibility)
                result = _build_pipeline(route)

                assert result is not None

                mock_emit.assert_called_once()
                call_kwargs = mock_emit.call_args.kwargs
                assert call_kwargs["req_id"] is None


# ---------------------------------------------------------------------------
# test_build_pipeline_no_filter_chain
# ---------------------------------------------------------------------------


def test_build_pipeline_no_filter_chain_uses_empty_pipeline():
    """Routes without filters still use the V2 pipeline."""
    route = MagicMock()
    route.filters = None

    result = _build_pipeline(route, req_id="req-123")
    assert result is not None
