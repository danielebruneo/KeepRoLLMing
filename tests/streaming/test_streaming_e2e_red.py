"""E2E red tests — fake backend → KRM streaming → mini-client parser.

These tests run a controlled fake upstream backend through the existing KRM
streaming endpoint, collect the downstream SSE emitted by KRM, parse it with
``tests/helpers/stream_client.py``, and assert protocol invariants.

Tests are expected to FAIL on V1 if KRM violates the spec.  Marked with
``pytest.mark.red`` so they can be filtered with ``-m red``.

Architecture:
    test → httpx → orchestrator (keeprollming.app) → httpx → fake_backend

Uses fixtures from ``tests/e2e/conftest.py`` (fake_backend_server,
orchestrator_server, backend_client, configure_fake_backend).

Test categories
---------------
Sanity passthrough (direct-forward, should PASS on V1):
    • ``test_e2e_done_is_last`` — uses ``local/main`` which has **no**
      ``filters`` in ``config.test.yaml``.  Verifies basic SSE framing
      (``[DONE]`` is last event).
    • ``test_e2e_rejects_content_after_finish`` — uses ``local/main`` (no
      ``filters``).  Verifies no assistant text appears after the
      finish event (clean stream).  Sanity check that the downstream SSE
      does not leak content past the finish marker.
    • ``test_e2e_tool_call_finish_alignment`` — uses ``local/main`` (no
      ``filters``).  Verifies tool-call + finish alignment (I1, I8, I9).

Filter / pipeline red tests (exercise real filter behaviour):
    • ``test_e2e_timestamp_before_finish`` — uses ``internal/timestamp`` route
      which **does** exercise the timestamp filter.  Verifies timestamp footer
      appears before Finish.
    • ``test_e2e_stale_timestamp_footer_replaced_not_duplicated`` — uses
      ``internal/timestamp`` route.  Verifies the timestamp filter strips the
      stale footer from the upstream and does **not** produce duplicates.
      This is a regression contract for the V2 timestamp finalizer.

V2 ToolRewrite e2e tests (full KRM pipeline coverage):
    • ``test_v2_tool_rewrite_valid_xml`` — uses ``tool-rewrite-v2`` route.
      Verifies valid XML is rewritten to ToolCallComplete, original XML
      suppressed, Finish.reason upgraded to "tool_calls", exactly one
      Finish and one Done, Done is last.
    • ``test_v2_tool_rewrite_fail_open_malformed_xml`` — uses
      ``tool-rewrite-v2`` route.  Verifies malformed XML passes through
      unchanged (fail-open), no ToolCallComplete, Finish.reason="stop",
      no content dropped.
    • ``test_v2_tool_rewrite_timestamp_coexistence`` — uses
      ``tool-rewrite-v2-timestamp`` route.  Verifies valid XML rewritten,
      original XML absent and that a tool-call-only terminal turn has no
      synthetic timestamp AssistantTextDelta.
"""

from __future__ import annotations

import httpx
import pytest

# Import e2e fixtures so pytest sees them as available for this module.
# pytest discovers fixtures from conftest.py files in the test directory tree;
# importing here ensures tests/e2e/conftest.py is loaded.
from tests.e2e.conftest import (  # noqa: F401
    fake_backend_server,
    backend_target,
    orchestrator_server,
    backend_client,
    configure_fake_backend,
    get_fake_stats,
)

from tests.helpers.stream_client import (
    TestAssistantTextDelta,
    TestDone,
    TestFinish,
    TestToolCallComplete,
    collect_assistant_text,
    collect_finish_events,
    collect_tool_calls,
    parse_sse_events,
    assert_stream_protocol_valid,
    _count_timestamp_footers,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_raw_chunks(response: httpx.Response) -> list[str]:
    """Collect SSE frames from a streaming httpx response as text strings.

    Iterates ``iter_bytes()`` (chunked wire bytes) and decodes each chunk
    to str.  This preserves every SSE frame boundary exactly as emitted
    by KRM — including ``data: {...}``, ``data: [DONE]``, and blank-line
    separators.

    ``parse_sse_events()`` accepts both ``str`` and ``bytes`` items.
    """
    chunks: list[str] = []
    for raw in response.iter_bytes():
        if not raw:
            continue
        chunks.append(raw.decode("utf-8", errors="replace"))
    return chunks


# ---------------------------------------------------------------------------
# Test 1: test_e2e_done_is_last
# ---------------------------------------------------------------------------

@pytest.mark.red
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestE2EDoneIsLast:
    """Sanity passthrough — fake upstream emits normal content + finish_reason stop + [DONE].

    Uses ``local/main`` which has **no** ``filters`` in ``config.test.yaml``,
    so this is a direct-forward sanity test.  It verifies basic SSE framing
    (``[DONE]`` is the last event) and passes full protocol validation.

    Expected to PASS on V1.
    """

    def test_e2e_done_is_last(
        self,
        orchestrator_server,
        backend_target,
        backend_client,
        configure_fake_backend,
    ):
        configure_fake_backend({
            "chat": {
                "stream_pieces": [["Hello, ", "world!"]],
                "include_usage": True,
            }
        })

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "local/main",
                "messages": [{"role": "user", "content": "Say hello"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200, resp.text

        # Sanity passthrough: local/main has no filters — direct forward.
        chunks = _collect_raw_chunks(resp)
        events = parse_sse_events(chunks)

        # Mini-client must see Done as the last event
        assert events, "No events parsed from downstream SSE"
        assert isinstance(events[-1], TestDone), (
            f"I2 violation: last event is {type(events[-1]).__name__}, "
            f"expected TestDone. Events: {[type(e).__name__ for e in events]}"
        )

        # Full protocol validation should pass
        assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test 2: test_e2e_rejects_content_after_finish
# ---------------------------------------------------------------------------

@pytest.mark.red
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestE2EContentAfterFinish:
    """Sanity passthrough — verifies no assistant text appears after the finish event.

    Uses ``local/main`` which has **no** ``filters`` in ``config.test.yaml``,
    so this is a direct-forward sanity test.  The fake backend emits two
    stream pieces (``["Hello", "World (retry)"]``) followed by finish_reason.
    The test asserts that neither piece appears after the TestFinish event
    in the parsed downstream SSE.

    Expected to PASS on V1 — the clean stream has no content after finish.
    """

    def test_e2e_rejects_content_after_finish(
        self,
        orchestrator_server,
        backend_target,
        backend_client,
        configure_fake_backend,
    ):
        # Fake backend: two stream pieces ("Hello", "World (retry)") followed
        # by finish_reason.  With local/main (no filters) the finish
        # marker is emitted after all pieces, so no content leaks past it.
        configure_fake_backend({
            "chat": {
                "stream_pieces": [["Hello", "World (retry)"]],
                "include_usage": True,
            }
        })

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "local/main",
                "messages": [{"role": "user", "content": "Say hello"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200, resp.text

        chunks = _collect_raw_chunks(resp)
        events = parse_sse_events(chunks)

        # Collect text to see what we got
        text = collect_assistant_text(events)
        finishes = collect_finish_events(events)

        # At minimum we should have exactly one finish
        assert len(finishes) == 1, (
            f"Expected exactly 1 finish, got {len(finishes)}. "
            f"Text: {text!r}. Events: {[type(e).__name__ for e in events]}"
        )

        finish_idx = next(
            i for i, e in enumerate(events) if isinstance(e, TestFinish)
        )

        # Check if there's content after finish (I4 violation)
        content_after = [
            e for i, e in enumerate(events)
            if i > finish_idx and isinstance(e, TestAssistantTextDelta)
        ]

        if content_after:
            # I4 violation — this is the expected red outcome on V1
            pytest.fail(
                "I4: Assistant text found after finish. "
                f"Text after finish: {collect_assistant_text(content_after)!r}. "
                f"Total text: {text!r}. "
                f"Events after finish: {[type(e).__name__ for e in content_after]}"
            )
        else:
            # Clean stream — no content leaked after finish
            assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test 3: test_e2e_timestamp_before_finish
# ---------------------------------------------------------------------------

@pytest.mark.red
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestE2ETimestampBeforeFinish:
    """Filter red test — fake upstream emits normal streaming content on the ``internal/timestamp`` route.

    The ``internal/timestamp`` route **does** have a filters configured
    in ``config.test.yaml``, so this test exercises the actual timestamp
    filter behaviour.

    Assert any timestamp footer emitted by KRM appears BEFORE Finish and
    BEFORE Done according to the mini-client events.

    Expected to PASS on V1 if the timestamp filter places the footer before
    the finish event.
    """

    def test_e2e_timestamp_before_finish(
        self,
        orchestrator_server,
        backend_target,
        backend_client,
        configure_fake_backend,
    ):
        configure_fake_backend({
            "chat": {
                "stream_pieces": [["Timestamp test content"]],
                "include_usage": True,
            }
        })

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/timestamp-v1",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200, resp.text

        chunks = _collect_raw_chunks(resp)
        events = parse_sse_events(chunks)

        text = collect_assistant_text(events)
        finishes = collect_finish_events(events)

        # We should have exactly one finish
        assert len(finishes) == 1, (
            f"Expected 1 finish, got {len(finishes)}. "
            f"Events: {[type(e).__name__ for e in events]}"
        )
        finish_idx = next(
            i for i, e in enumerate(events) if isinstance(e, TestFinish)
        )

        # If there's a timestamp footer, it must be before finish
        ts_count = _count_timestamp_footers(text)
        if ts_count >= 1:
            # I7: timestamp must be before finish
            last_content_idx = -1
            for i, e in enumerate(events):
                if isinstance(e, TestAssistantTextDelta):
                    last_content_idx = i
            if last_content_idx > finish_idx:
                pytest.fail(
                    "I7: Timestamp footer appears after finish. "
                    f"Content at {last_content_idx}, finish at {finish_idx}. "
                    f"Text: {text!r}"
                )

            # Full validator should pass if timestamp is properly placed
            assert_stream_protocol_valid(events, profile="strict")
        else:
            # No timestamp footer — the filter may not be active or the route
            # may not have the timestamp filter configured.
            pytest.skip(
                "No timestamp footer found in downstream content. "
                f"Text: {text!r}. Events: {[type(e).__name__ for e in events]}"
            )


# ---------------------------------------------------------------------------
# Test 4: test_e2e_stale_timestamp_footer_replaced_not_duplicated
# ---------------------------------------------------------------------------

@pytest.mark.red
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestE2ETimestampFooterDedup:
    """Filter red test — fake upstream emits assistant content ending with a stale timestamp footer.

    The ``internal/timestamp-v1`` route **does** have a filters configured,
    so this test exercises the actual timestamp filter behaviour.

    KRM timestamp handling should not produce two timestamp footers.
    Uses mini-client collected text and timestamp counting helper.

    Verifies I6 (timestamp appears at most once) and I7 (timestamp before Finish).

    The V2 timestamp finalizer removes a stale upstream footer before emitting
    exactly one fresh footer to the downstream client.
    """

    def test_e2e_stale_timestamp_footer_replaced_not_duplicated(
        self,
        orchestrator_server,
        backend_target,
        backend_client,
        configure_fake_backend,
    ):
        # Backend returns content that already has a timestamp footer
        stale_ts = "\n\n---\nTimestamp: 2020-01-01 00:00:00 UTC"
        configure_fake_backend({
            "chat": {
                "stream_pieces": [["Hello" + stale_ts]],
                "include_usage": True,
            }
        })

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/timestamp-v1",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200, resp.text

        chunks = _collect_raw_chunks(resp)
        events = parse_sse_events(chunks)

        text = collect_assistant_text(events)
        ts_count = _count_timestamp_footers(text)

        # I6: at most one timestamp footer
        if ts_count > 1:
            pytest.fail(
                "I6: Duplicate timestamp footer in downstream content. "
                f"Count: {ts_count}. Text: {text!r}"
            )

        # Should still have at least one timestamp (the fresh one from KRM)
        assert ts_count >= 1, (
            "Expected at least one timestamp footer after KRM processing. "
            f"Text: {text!r}. Events: {[type(e).__name__ for e in events]}"
        )

        # I7: timestamp must appear before Finish
        finish_idx = next(
            i for i, e in enumerate(events) if isinstance(e, TestFinish)
        )
        last_content_idx = -1
        for i, e in enumerate(events):
            if isinstance(e, TestAssistantTextDelta):
                last_content_idx = i
        if last_content_idx > finish_idx:
            pytest.fail(
                "I7: Timestamp footer appears after finish. "
                f"Content at {last_content_idx}, finish at {finish_idx}. "
                f"Text: {text!r}"
            )

        # Full protocol validation (strict: internal/timestamp route, no tool calls)
        assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test 5: test_e2e_tool_call_finish_alignment
# ---------------------------------------------------------------------------

@pytest.mark.red
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestE2EToolCallFinishAlignment:
    """Sanity passthrough — fake upstream emits a complete tool_call and finish_reason=tool_calls.

    Uses ``local/main`` which has **no** ``filters`` in ``config.test.yaml``,
    so this is a direct-forward sanity test.  It verifies tool-call + finish
    alignment invariants (I1, I8, I9).

    Expected to PASS on V1.
    """

    def test_e2e_tool_call_finish_alignment(
        self,
        orchestrator_server,
        backend_target,
        backend_client,
        configure_fake_backend,
    ):
        configure_fake_backend({
            "chat": {
                "tool_calls": [
                    {
                        "id": "call_abc123",
                        "type": "function",
                        "function": {
                            "name": "search_files",
                            "arguments": '{"query": "important.pdf", "path": "/docs"}',
                        },
                    }
                ],
                "include_usage": True,
            }
        })

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "local/main",
                "messages": [{"role": "user", "content": "Search for files"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200, resp.text

        chunks = _collect_raw_chunks(resp)
        events = parse_sse_events(chunks)

        finishes = collect_finish_events(events)
        tool_calls = collect_tool_calls(events)

        # I1: exactly one finish
        assert len(finishes) == 1, (
            f"Expected 1 finish, got {len(finishes)}. "
            f"Events: {[type(e).__name__ for e in events]}"
        )

        # I9: finish_reason must be tool_calls when tool calls present
        assert finishes[0].reason == "tool_calls", (
            f"I9: finish_reason is {finishes[0].reason!r}, expected 'tool_calls'. "
            f"Events: {[type(e).__name__ for e in events]}"
        )

        # I9: at least one complete tool call with valid JSON
        assert len(tool_calls) >= 1, (
            "I9: finish_reason=tool_calls but no complete tool calls emitted. "
            f"Events: {[type(e).__name__ for e in events]}"
        )

        # Verify arguments are valid JSON
        for tc in tool_calls:
            assert tc.arguments_obj is not None, (
                f"I8: Tool call '{tc.name}' has invalid JSON arguments: "
                f"{tc.arguments_json!r}"
            )

        # local/main has no filters so content+tool_call may share
        # the same delta — use lenient profile for this sanity test.
        assert_stream_protocol_valid(events, profile="lenient")


# ---------------------------------------------------------------------------
# Test 6: test_v2_e2e_done_is_last
# ---------------------------------------------------------------------------

@pytest.mark.red
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestV2E2EDoneIsLast:
    """V2 e2e — uses ``internal/timestamp-v2`` route.  Verifies basic SSE framing.

    Expected to PASS on V2 (TimestampFinalizer tail-buffer → no duplicate footer).
    """

    def test_v2_e2e_done_is_last(
        self,
        orchestrator_server,
        backend_target,
        backend_client,
        configure_fake_backend,
    ):
        configure_fake_backend({
            "chat": {
                "stream_pieces": [["Hello, ", "world!"]],
                "include_usage": True,
            }
        })

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/timestamp-v2",
                "messages": [{"role": "user", "content": "Say hello"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200, resp.text

        chunks = _collect_raw_chunks(resp)
        events = parse_sse_events(chunks)

        assert events, "No events parsed from downstream SSE"
        assert isinstance(events[-1], TestDone), (
            f"I2 violation: last event is {type(events[-1]).__name__}, "
            f"expected TestDone. Events: {[type(e).__name__ for e in events]}"
        )
        assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test 7: test_v2_e2e_stale_timestamp_footer_replaced_not_duplicated
# ---------------------------------------------------------------------------

@pytest.mark.red
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestV2E2ETimestampFooterDedup:
    """V2 e2e — fake upstream emits assistant content ending with a stale timestamp footer.

    Uses ``internal/timestamp-v2`` route which exercises the V2
    ``TimestampFinalizer`` with tail-buffer dedup.

    Expected to PASS on V2 — stale footer is stripped, one fresh footer appended.
    """

    def test_v2_e2e_stale_timestamp_footer_replaced_not_duplicated(
        self,
        orchestrator_server,
        backend_target,
        backend_client,
        configure_fake_backend,
    ):
        stale_ts = "\n\n---\nTimestamp: 2020-01-01 00:00:00 UTC"
        configure_fake_backend({
            "chat": {
                "stream_pieces": [["Hello" + stale_ts]],
                "include_usage": True,
            }
        })

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/timestamp-v2",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200, resp.text

        chunks = _collect_raw_chunks(resp)
        events = parse_sse_events(chunks)

        text = collect_assistant_text(events)
        ts_count = _count_timestamp_footers(text)

        # I6: at most one timestamp footer
        if ts_count > 1:
            pytest.fail(
                "I6: Duplicate timestamp footer in downstream content. "
                f"Count: {ts_count}. Text: {text!r}"
            )

        # Should have at least one timestamp (the fresh one from V2 finalizer)
        assert ts_count >= 1, (
            "Expected at least one timestamp footer after V2 processing. "
            f"Text: {text!r}. Events: {[type(e).__name__ for e in events]}"
        )

        # I7: timestamp must appear before Finish
        finishes = collect_finish_events(events)
        assert len(finishes) == 1, (
            f"Expected 1 finish, got {len(finishes)}. "
            f"Events: {[type(e).__name__ for e in events]}"
        )
        finish_idx = next(
            i for i, e in enumerate(events) if isinstance(e, TestFinish)
        )
        last_content_idx = -1
        for i, e in enumerate(events):
            if isinstance(e, TestAssistantTextDelta):
                last_content_idx = i
        if last_content_idx > finish_idx:
            pytest.fail(
                "I7: Timestamp footer appears after finish. "
                f"Content at {last_content_idx}, finish at {finish_idx}. "
                f"Text: {text!r}"
            )

        assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test 8: test_v2_e2e_timestamp_before_finish
# ---------------------------------------------------------------------------

@pytest.mark.red
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestV2E2ETimestampBeforeFinish:
    """V2 e2e — normal content with ``internal/timestamp-v2`` route.

    Verifies timestamp footer appears before Finish.
    Expected to PASS on V2.
    """

    def test_v2_e2e_timestamp_before_finish(
        self,
        orchestrator_server,
        backend_target,
        backend_client,
        configure_fake_backend,
    ):
        configure_fake_backend({
            "chat": {
                "stream_pieces": [["Timestamp test content"]],
                "include_usage": True,
            }
        })

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/timestamp-v2",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200, resp.text

        chunks = _collect_raw_chunks(resp)
        events = parse_sse_events(chunks)

        text = collect_assistant_text(events)
        finishes = collect_finish_events(events)

        assert len(finishes) == 1, (
            f"Expected 1 finish, got {len(finishes)}. "
            f"Events: {[type(e).__name__ for e in events]}"
        )
        finish_idx = next(
            i for i, e in enumerate(events) if isinstance(e, TestFinish)
        )

        ts_count = _count_timestamp_footers(text)
        if ts_count >= 1:
            last_content_idx = -1
            for i, e in enumerate(events):
                if isinstance(e, TestAssistantTextDelta):
                    last_content_idx = i
            if last_content_idx > finish_idx:
                pytest.fail(
                    "I7: Timestamp footer appears after finish. "
                    f"Content at {last_content_idx}, finish at {finish_idx}. "
                    f"Text: {text!r}"
                )
            assert_stream_protocol_valid(events, profile="strict")
        else:
            pytest.fail(
                "V2 timestamp-v2 route must produce a timestamp footer. "
                f"ts_count={ts_count}. Text: {text!r}. "
                f"Events: {[type(e).__name__ for e in events]}"
            )


# ---------------------------------------------------------------------------
# Test 9: test_v2_e2e_done_is_last_strict
# ---------------------------------------------------------------------------

@pytest.mark.red
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestV2E2EDoneIsLastStrict:
    """V2 e2e — strict protocol validation on ``internal/timestamp-v2`` route.

    Verifies Done is last and strict protocol is valid.
    Expected to PASS on V2.
    """

    def test_v2_e2e_done_is_last_strict(
        self,
        orchestrator_server,
        backend_target,
        backend_client,
        configure_fake_backend,
    ):
        configure_fake_backend({
            "chat": {
                "stream_pieces": [["Strict protocol test"]],
                "include_usage": True,
            }
        })

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/timestamp-v2",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200, resp.text

        chunks = _collect_raw_chunks(resp)
        events = parse_sse_events(chunks)

        # Done must be last
        assert events, "No events parsed from downstream SSE"
        assert isinstance(events[-1], TestDone), (
            f"I2 violation: last event is {type(events[-1]).__name__}, "
            f"expected TestDone. Events: {[type(e).__name__ for e in events]}"
        )

        # Strict protocol validation
        assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test 10: test_v2_e2e_tool_call_assembly
# ---------------------------------------------------------------------------

@pytest.mark.red
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestV2E2EToolCallAssembly:
    """V2 e2e — fake upstream emits tool-call delta + finish_reason=tool_calls.

    Uses ``internal/timestamp-v2`` route which exercises the V2
    ``ToolCallFinalizer`` that assembles ToolCallDelta → ToolCallComplete.

    Expected to PASS on V2: ToolCallComplete appears before Finish.
    """

    def test_v2_e2e_tool_call_assembly(
        self,
        orchestrator_server,
        backend_target,
        backend_client,
        configure_fake_backend,
    ):
        configure_fake_backend({
            "chat": {
                "tool_calls": [
                    {
                        "id": "call_abc123",
                        "type": "function",
                        "function": {
                            "name": "search_files",
                            "arguments": '{"query": "important.pdf", "path": "/docs"}',
                        },
                    }
                ],
                "include_usage": True,
            }
        })

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/timestamp-v2",
                "messages": [{"role": "user", "content": "Search for files"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200, resp.text

        chunks = _collect_raw_chunks(resp)
        events = parse_sse_events(chunks)

        finishes = collect_finish_events(events)
        tool_calls = collect_tool_calls(events)

        # I1: exactly one finish
        assert len(finishes) == 1, (
            f"Expected 1 finish, got {len(finishes)}. "
            f"Events: {[type(e).__name__ for e in events]}"
        )

        # I9: finish_reason must be tool_calls when tool calls present
        assert finishes[0].reason == "tool_calls", (
            f"I9: finish_reason is {finishes[0].reason!r}, expected 'tool_calls'. "
            f"Events: {[type(e).__name__ for e in events]}"
        )

        # I9: at least one complete tool call with valid JSON
        assert len(tool_calls) >= 1, (
            "I9: finish_reason=tool_calls but no complete tool calls emitted. "
            f"Events: {[type(e).__name__ for e in events]}"
        )

        # Verify arguments are valid JSON
        for tc in tool_calls:
            assert tc.arguments_obj is not None, (
                f"I8: Tool call '{tc.name}' has invalid JSON arguments: "
                f"{tc.arguments_json!r}"
            )

        # Full protocol validation (lenient: content+tool_call may share delta)
        assert_stream_protocol_valid(events, profile="lenient")


# ---------------------------------------------------------------------------
# Test 11: test_v2_e2e_tool_call_no_finish_synthetic
# ---------------------------------------------------------------------------

@pytest.mark.red
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestV2E2EToolCallNoFinish:
    """V2 e2e — fake upstream emits tool-call delta + [DONE] but no Finish.

    Tests the runner's synthetic Finish emission: when no Finish event is
    received but tool calls were buffered, the runner emits ToolCallComplete
    followed by a synthetic Finish(reason="tool_calls").

    Expected to PASS on V2.
    """

    def test_v2_e2e_tool_call_no_finish_synthetic(
        self,
        orchestrator_server,
        backend_target,
        backend_client,
        configure_fake_backend,
    ):
        configure_fake_backend({
            "chat": {
                "tool_calls": [
                    {
                        "id": "call_xyz789",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "Rome", "unit": "celsius"}',
                        },
                    }
                ],
                "final_finish_reason": None,  # No finish_reason — runner must synthesize
                "include_usage": True,
            }
        })

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/timestamp-v2",
                "messages": [{"role": "user", "content": "What's the weather?"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200, resp.text

        chunks = _collect_raw_chunks(resp)
        events = parse_sse_events(chunks)

        finishes = collect_finish_events(events)
        tool_calls = collect_tool_calls(events)

        # I1: exactly one finish (synthetic)
        assert len(finishes) == 1, (
            f"Expected 1 finish (synthetic), got {len(finishes)}. "
            f"Events: {[type(e).__name__ for e in events]}"
        )

        # I9: synthetic Finish must be tool_calls when tool calls present
        assert finishes[0].reason == "tool_calls", (
            f"I9: synthetic finish_reason is {finishes[0].reason!r}, expected 'tool_calls'. "
            f"Events: {[type(e).__name__ for e in events]}"
        )

        # I9: at least one complete tool call
        assert len(tool_calls) >= 1, (
            "I9: tool calls present but no complete tool calls emitted. "
            f"Events: {[type(e).__name__ for e in events]}"
        )

        # Done must be last
        assert events and isinstance(events[-1], TestDone), (
            "I2: [DONE] not last"
        )

        # Full protocol validation (lenient: content+tool_call may share delta)
        assert_stream_protocol_valid(events, profile="lenient")


# ---------------------------------------------------------------------------
# Test 12: test_v2_e2e_non_tool_normal
# ---------------------------------------------------------------------------

@pytest.mark.red
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestV2E2ENonToolNormal:
    """V2 e2e — normal non-tool V2 route: text content + finish_reason=stop.

    Verifies that a non-tool V2 stream produces valid downstream SSE with
    ToolCallFinalizer present but no ToolCallComplete emitted.

    Expected to PASS on V2.
    """

    def test_v2_e2e_non_tool_normal(
        self,
        orchestrator_server,
        backend_target,
        backend_client,
        configure_fake_backend,
    ):
        configure_fake_backend({
            "chat": {
                "stream_pieces": [["Normal assistant response"]],
                "include_usage": True,
            }
        })

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/timestamp-v2",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200, resp.text

        chunks = _collect_raw_chunks(resp)
        events = parse_sse_events(chunks)

        finishes = collect_finish_events(events)
        tool_calls = collect_tool_calls(events)
        text = collect_assistant_text(events)

        # I1: exactly one finish
        assert len(finishes) == 1, (
            f"Expected 1 finish, got {len(finishes)}. "
            f"Events: {[type(e).__name__ for e in events]}"
        )

        # Normal finish reason is "stop"
        assert finishes[0].reason == "stop", (
            f"Expected finish_reason='stop', got {finishes[0].reason!r}. "
            f"Events: {[type(e).__name__ for e in events]}"
        )

        # No tool calls in a normal text response
        assert len(tool_calls) == 0, (
            f"Expected no tool calls, got {len(tool_calls)}. "
            f"Events: {[type(e).__name__ for e in events]}"
        )

        # Text content must be present
        assert text, "Expected assistant text content"

        # Done must be last
        assert events and isinstance(events[-1], TestDone), (
            "I2: [DONE] not last"
        )

        # Full protocol validation
        assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test 13: test_v2_e2e_timestamp_tool_call_coexistence
# ---------------------------------------------------------------------------

@pytest.mark.red
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestV2E2ETimestampToolCallCoexistence:
    """V2 e2e — tool-call V2 route with timestamp filter.

    Verifies that when both TimestampFinalizer and ToolCallFinalizer are
    active, tool calls are assembled correctly and the timestamp footer
    appears before Finish.

    Note: OpenAI-style tool-call responses usually do not mix assistant
    text content and tool calls under strict profile (I10), so this test
    uses the "lenient" profile to allow the timestamp footer (appended by
    TimestampFinalizer to content) alongside tool calls.

    Expected to PASS on V2.
    """

    def test_v2_e2e_timestamp_tool_call_coexistence(
        self,
        orchestrator_server,
        backend_target,
        backend_client,
        configure_fake_backend,
    ):
        configure_fake_backend({
            "chat": {
                "stream_pieces": [["Tool call with timestamp"]],
                "tool_calls": [
                    {
                        "id": "call_tc001",
                        "type": "function",
                        "function": {
                            "name": "list_files",
                            "arguments": '{"path": "/tmp"}',
                        },
                    }
                ],
                "include_usage": True,
            }
        })

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/timestamp-v2",
                "messages": [{"role": "user", "content": "List files"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200, resp.text

        chunks = _collect_raw_chunks(resp)
        events = parse_sse_events(chunks)

        finishes = collect_finish_events(events)
        tool_calls = collect_tool_calls(events)
        text = collect_assistant_text(events)

        # I1: exactly one finish
        assert len(finishes) == 1, (
            f"Expected 1 finish, got {len(finishes)}. "
            f"Events: {[type(e).__name__ for e in events]}"
        )

        # I9: finish_reason must be tool_calls
        assert finishes[0].reason == "tool_calls", (
            f"I9: finish_reason is {finishes[0].reason!r}, expected 'tool_calls'. "
            f"Events: {[type(e).__name__ for e in events]}"
        )

        # At least one complete tool call
        assert len(tool_calls) >= 1, (
            "I9: tool calls present but no complete tool calls. "
            f"Events: {[type(e).__name__ for e in events]}"
        )

        # Timestamp footer must be present (from TimestampFinalizer)
        ts_count = _count_timestamp_footers(text)
        assert ts_count >= 1, (
            "Expected at least one timestamp footer. "
            f"Text: {text!r}. Events: {[type(e).__name__ for e in events]}"
        )

        # I6: at most one timestamp footer
        if ts_count > 1:
            pytest.fail(
                "I6: Duplicate timestamp footer. "
                f"Count: {ts_count}. Text: {text!r}"
            )

        # I9: ToolCallComplete before Finish
        has_tc_before_finish = False
        finish_idx = next(
            i for i, e in enumerate(events) if isinstance(e, TestFinish)
        )
        for i, e in enumerate(events):
            if i < finish_idx and isinstance(e, TestToolCallComplete):
                has_tc_before_finish = True
                break
        assert has_tc_before_finish, (
            "ToolCallComplete must appear before Finish. "
            f"Events: {[type(e).__name__ for e in events]}"
        )

        # Full protocol validation (lenient: content+tool_call may share context)
        assert_stream_protocol_valid(events, profile="lenient")


# ---------------------------------------------------------------------------
# Test 9: test_v2_tool_rewrite_valid_xml
# ---------------------------------------------------------------------------

@pytest.mark.red
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestV2ToolRewriteValidXML:
    """V2 e2e — fake backend emits valid XML pseudo-tool-call through
    ``tool-rewrite-v2`` route.  Verifies full KRM pipeline rewriting.

    Expected to PASS on V2 — ToolRewriteFinalizer rewrites XML to
    ToolCallDelta, ToolCallFinalizer emits ToolCallComplete, Finish.reason
    upgraded to "tool_calls".
    """

    def test_v2_tool_rewrite_valid_xml(
        self,
        orchestrator_server,
        backend_target,
        backend_client,
        configure_fake_backend,
    ):
        # Fake backend emits valid XML pseudo-tool-call
        configure_fake_backend({
            "chat": {
                "stream_pieces": [["<read><path>README.md</path></read>"]],
                "include_usage": True,
            }
        })

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "tool-rewrite-v2",
                "messages": [{"role": "user", "content": "Read the file"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200, resp.text

        chunks = _collect_raw_chunks(resp)
        events = parse_sse_events(chunks)

        assert events, "No events parsed from downstream SSE"

        # 1. ToolCallComplete emitted with correct tool name and arguments
        tcc_events = [e for e in events if isinstance(e, TestToolCallComplete)]
        assert len(tcc_events) == 1, (
            f"Expected exactly one ToolCallComplete, got {len(tcc_events)}. "
            f"Events: {[type(e).__name__ for e in events]}"
        )
        tcc = tcc_events[0]
        assert tcc.name == "read", (
            f"Expected tool name 'read', got '{tcc.name}'."
        )
        assert tcc.arguments_json == '{"path":"README.md"}', (
            f"Expected arguments '{{\"path\":\"README.md\"}}', "
            f"got '{tcc.arguments_json}'."
        )
        assert tcc.arguments_obj is not None, (
            "ToolCallComplete arguments should be valid JSON."
        )

        # 2. Original XML is NOT downstream
        xml_events = [
            e for e in events
            if isinstance(e, TestAssistantTextDelta) and "<read>" in e.delta
        ]
        assert len(xml_events) == 0, (
            f"Original XML must be suppressed, but found {len(xml_events)} "
            f"events with '<read>' content."
        )

        # 3. Finish.reason is "tool_calls"
        finishes = collect_finish_events(events)
        assert len(finishes) == 1, (
            f"Expected exactly one Finish, got {len(finishes)}. "
            f"Events: {[type(e).__name__ for e in events]}"
        )
        assert finishes[0].reason == "tool_calls", (
            f"Expected Finish.reason='tool_calls', got '{finishes[0].reason}'."
        )

        # 4. Done is last and exactly one Done
        assert isinstance(events[-1], TestDone), (
            "Done must be the last event."
        )
        done_events = [e for e in events if isinstance(e, TestDone)]
        assert len(done_events) == 1, (
            f"Expected exactly one Done event, got {len(done_events)}."
        )

        # Full protocol validation (lenient: content+tool_call may share)
        assert_stream_protocol_valid(events, profile="lenient")


# ---------------------------------------------------------------------------
# Test 10: test_v2_tool_rewrite_fail_open_malformed_xml
# ---------------------------------------------------------------------------

@pytest.mark.red
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestV2ToolRewriteFailOpenMalformedXML:
    """V2 e2e — fake backend emits malformed XML with unescaped & through
    ``tool-rewrite-v2`` route.  Verifies fail-open behavior.

    Expected to PASS on V2 — malformed XML passes through unchanged,
    no ToolCallComplete emitted, Finish.reason="stop".
    """

    def test_v2_tool_rewrite_fail_open_malformed_xml(
        self,
        orchestrator_server,
        backend_target,
        backend_client,
        configure_fake_backend,
    ):
        # Fake backend emits malformed XML: unescaped & breaks XML parsing
        configure_fake_backend({
            "chat": {
                "stream_pieces": [["<read>path=foo&bar</read>"]],
                "include_usage": True,
            }
        })

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "tool-rewrite-v2",
                "messages": [{"role": "user", "content": "Read the file"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200, resp.text

        chunks = _collect_raw_chunks(resp)
        events = parse_sse_events(chunks)

        # 1. No ToolCallComplete emitted
        tcc_events = [e for e in events if isinstance(e, TestToolCallComplete)]
        assert len(tcc_events) == 0, (
            f"Malformed XML should NOT produce ToolCallComplete, but got "
            f"{len(tcc_events)}. Events: {[type(e).__name__ for e in events]}"
        )

        # 2. Original text passes through (fail-open)
        text_events = [
            e for e in events if isinstance(e, TestAssistantTextDelta)
        ]
        assert len(text_events) > 0, (
            "Expected at least one AssistantTextDelta for fail-open."
        )
        has_malformed = any("<read>" in e.delta for e in text_events)
        assert has_malformed, (
            "Malformed XML should pass through unchanged (fail-open)."
        )

        # 3. Finish.reason is "stop" (no tool calls)
        finishes = collect_finish_events(events)
        assert len(finishes) == 1, (
            f"Expected exactly one Finish, got {len(finishes)}. "
            f"Events: {[type(e).__name__ for e in events]}"
        )
        assert finishes[0].reason == "stop", (
            f"Expected Finish.reason='stop' for malformed XML, "
            f"got '{finishes[0].reason}'."
        )

        # 4. No content dropped — the malformed XML should be present
        all_text = collect_assistant_text(events)
        assert "<read>path=foo&bar</read>" in all_text, (
            f"Malformed XML content should be preserved. Text: {all_text!r}"
        )

        # Full protocol validation
        assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test 11: test_v2_tool_rewrite_timestamp_coexistence
# ---------------------------------------------------------------------------

@pytest.mark.red
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestV2ToolRewriteTimestampCoexistence:
    """V2 e2e — fake backend emits valid XML through ``tool-rewrite-v2-timestamp``
    route (ToolRewrite + Timestamp).

    Verifies Timestamp's tool-call-only safety with ToolRewrite:
    - Valid XML rewritten to ToolCallComplete
    - Original XML absent
    - No timestamp-only AssistantTextDelta is created
    - Finish(tool_calls) remains a strict OpenAI-compatible terminal turn

    Expected to PASS on V2.
    """

    def test_v2_tool_rewrite_timestamp_coexistence(
        self,
        orchestrator_server,
        backend_target,
        backend_client,
        configure_fake_backend,
    ):
        # Fake backend emits valid XML pseudo-tool-call
        configure_fake_backend({
            "chat": {
                "stream_pieces": [["<read><path>README.md</path></read>"]],
                "include_usage": True,
            }
        })

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "tool-rewrite-v2-timestamp",
                "messages": [{"role": "user", "content": "Read the file"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200, resp.text

        chunks = _collect_raw_chunks(resp)
        events = parse_sse_events(chunks)

        # 1. ToolCallComplete emitted
        tcc_events = [e for e in events if isinstance(e, TestToolCallComplete)]
        assert len(tcc_events) == 1, (
            f"Expected exactly one ToolCallComplete, got {len(tcc_events)}. "
            f"Events: {[type(e).__name__ for e in events]}"
        )

        # 2. Finish.reason is "tool_calls"
        finishes = collect_finish_events(events)
        assert len(finishes) == 1, (
            f"Expected exactly one Finish, got {len(finishes)}. "
            f"Events: {[type(e).__name__ for e in events]}"
        )
        assert finishes[0].reason == "tool_calls", (
            f"Expected Finish.reason='tool_calls', got '{finishes[0].reason}'."
        )

        # 3. Original XML is NOT downstream
        xml_events = [
            e for e in events
            if isinstance(e, TestAssistantTextDelta) and "<read>" in e.delta
        ]
        assert len(xml_events) == 0, (
            f"Original XML must be suppressed, but found {len(xml_events)} "
            f"events with '<read>' content."
        )

        # 4. Tool-call-only turns must not acquire synthetic timestamp text.
        all_text = collect_assistant_text(events)
        ts_count = _count_timestamp_footers(all_text)
        assert ts_count == 0, (
            f"Expected no timestamp footer on a tool-call-only turn, found {ts_count}. "
            f"Text: {all_text!r}. Events: {[type(e).__name__ for e in events]}"
        )

        # 5. No assistant text may be sent before the tool-call finish.
        finish_idx = next(
            i for i, e in enumerate(events) if isinstance(e, TestFinish)
        )
        assert not any(
            isinstance(event, TestAssistantTextDelta)
            for event in events[:finish_idx]
        ), "A tool-call-only terminal turn must not contain assistant text"

        # 6. Done is last and exactly one Done
        assert isinstance(events[-1], TestDone), "Done must be the last event"
        done_events = [e for e in events if isinstance(e, TestDone)]
        assert len(done_events) == 1, (
            f"Expected exactly one Done event, got {len(done_events)}."
        )

        # Full protocol validation: this is now strict OpenAI-compatible SSE.
        assert_stream_protocol_valid(events, profile="strict")
