import pytest


@pytest.mark.asyncio
async def test_summary_middle_recursively_chunks_on_repeated_overflow(monkeypatch):
    import keeprollming.rolling_summary as rs

    calls = []

    async def fake_request(body):
        calls.append(body)
        user_text = str(body['messages'][-1]['content'])
        if len(user_text) > 2500:
            return {
                'error': {
                    'details': {
                        'response': {
                            'error': {
                                'code': 400,
                                'message': 'request (4473 tokens) exceeds the available context size (4096 tokens), try increasing it',
                                'n_ctx': 4096,
                                'type': 'exceed_context_size_error',
                            }
                        },
                        'status_code': 400,
                    },
                    'message': 'llama-server request failed',
                    'type': 'backend_error',
                }
            }
        return {'choices': [{'message': {'content': 'SUMMARY OK'}}], 'usage': {}}

    async def fake_get_ctx(_model):
        return 4096

    monkeypatch.setattr(rs, '_request_summary_completion', fake_request)
    monkeypatch.setattr(rs, 'get_ctx_len_for_model', fake_get_ctx)
    monkeypatch.setattr(rs, '_chunk_messages_for_summary', lambda messages, **kwargs: [messages])

    msgs = [{'role': 'user', 'content': 'A' * 12000}]
    out = await rs.summarize_middle(msgs, req_id='overflow', summary_model='sum-model')
    assert out == 'SUMMARY OK'
    assert len(calls) >= 2


@pytest.mark.asyncio
async def test_summary_incremental_recursively_chunks_on_repeated_overflow(monkeypatch):
    import keeprollming.rolling_summary as rs

    calls = []

    async def fake_request(body):
        calls.append(body)
        user_text = str(body['messages'][-1]['content'])
        if len(user_text) > 2500:
            return {
                'error': {
                    'details': {
                        'response': {
                            'error': {
                                'code': 400,
                                'message': 'request (4473 tokens) exceeds the available context size (4096 tokens), try increasing it',
                                'n_ctx': 4096,
                                'type': 'exceed_context_size_error',
                            }
                        },
                        'status_code': 400,
                    },
                    'message': 'llama-server request failed',
                    'type': 'backend_error',
                }
            }
        return {'choices': [{'message': {'content': 'UPDATED SUMMARY'}}], 'usage': {}}

    async def fake_get_ctx(_model):
        return 4096

    monkeypatch.setattr(rs, '_request_summary_completion', fake_request)
    monkeypatch.setattr(rs, 'get_ctx_len_for_model', fake_get_ctx)
    monkeypatch.setattr(rs, '_chunk_messages_for_summary', lambda messages, **kwargs: [messages])

    msgs = [{'role': 'user', 'content': 'B' * 12000}]
    out = await rs.summarize_incremental('seed', msgs, req_id='overflow-inc', summary_model='sum-model')
    assert out == 'UPDATED SUMMARY'
    assert len(calls) >= 2


@pytest.mark.asyncio
async def test_summary_middle_preflight_chunks_before_400(monkeypatch):
    import keeprollming.rolling_summary as rs

    calls = []

    async def fake_request(body):
        calls.append(body)
        return {'choices': [{'message': {'content': 'SUMMARY OK'}}], 'usage': {}}

    async def fake_get_ctx(_model):
        return 4096

    monkeypatch.setattr(rs, '_request_summary_completion', fake_request)
    monkeypatch.setattr(rs, 'get_ctx_len_for_model', fake_get_ctx)
    monkeypatch.setattr(rs, '_estimate_tokens_for_msgs', lambda tok, msgs: 10000 if len(str(msgs[-1].get('content', ''))) > 2500 else 1000)
    monkeypatch.setattr(rs, '_chunk_messages_for_summary', lambda messages, **kwargs: [messages[:1], messages[1:]] if len(messages) > 1 else [messages])

    msgs = [{'role': 'user', 'content': 'A' * 2000}, {'role': 'user', 'content': 'B' * 2000}]
    out = await rs.summarize_middle(msgs, req_id='preflight', summary_model='sum-model')
    assert out == 'SUMMARY OK'
    assert len(calls) >= 2
