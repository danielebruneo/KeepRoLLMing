    sent_msgs = fake.last_post_json["messages"]def test_web_search_payload_can_still_trigger_summary(client, monkeypatch):
    async def _fake_summary(_middle, **kwargs):
        return "WEB-SUMMARY"

    monkeypatch.setattr(app_mod, "summarize_middle", _fake_summary)

    long_text = "z" * 5000
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "local/main",
            "messages": [
                {
                    "role": "system",
                    "content": "# `web_search`:\nExecute immediately without preface.",
                },
                {"role": "user", "content": [{"type": "text", "text": long_text}]},
                {"role": "assistant", "content": "ack"},
                {"role": "tool", "name": "web_search", "tool_call_id": "t1", "content": "results"},
                {"role": "user", "content": "final question"},
            ],
            "tools": [{"type": "function", "function": {"name": "web_search", "parameters": {}}}],
            "max_tokens": 64,
        },
    )
    assert resp.status_code == 200, resp.text

    fake = _get_fake_upstream()
    sent_msgs = fake.last_post_json["messages"]

    joined = json.dumps(sent_msgs, ensure_ascii=False)
    assert "[ARCHIVED_COMPACT_CONTEXT]" in joined
    assert "WEB-SUMMARY" in joined
