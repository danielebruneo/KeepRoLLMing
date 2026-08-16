"""V2 is the only streaming pipeline."""

from keeprollming.orchestrator.pipeline import Pipeline


def test_pipeline_uses_runner_unconditionally(monkeypatch):
    pipeline = Pipeline()
    called = []

    async def fake_v2(*args, **kwargs):
        called.append((args, kwargs))
        yield b"data: [DONE]\\n\\n"

    monkeypatch.setattr(pipeline, "run_stream", fake_v2)

    async def consume():
        return [chunk async for chunk in pipeline.run_stream({}, "r1", "model")]

    import asyncio
    assert asyncio.run(consume()) == [b"data: [DONE]\\n\\n"]
    assert len(called) == 1


def test_pipeline_has_no_legacy_raw_chunk_runner():
    """Streaming must have one executable implementation: the V2 runner."""
    assert not hasattr(Pipeline, "_process_stream_with_filters")


def test_enabled_filter_names_uses_effective_priority_order():
    assert Pipeline.enabled_filter_names({
        "timestamp": {"enabled": True},
        "system_prompt": {"enabled": True},
        "model_nudge": {"enabled": False},
    }) == ["system_prompt", "timestamp"]


def test_empty_config_does_not_create_a_configured_pipeline():
    assert Pipeline.from_route_config(None) is None
