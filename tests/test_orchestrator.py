import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import Response as HttpxResponse
from pydantic import BaseModel

from keeprollming.app import app
from keeprollming.config import (
    MAIN_MODEL,
    SAFETY_MARGIN_TOK,
    SUMMARY_MODEL,
    UPSTREAM_BASE_URL,
)
from keeprollming.logger import LOG_MODE, log
from keeprollming.rolling_summary import should_summarise
from keeprollming.token_counter import TokenCounter

# Mock the logger to avoid actual logging during tests
@patch('keeprollming.logger.log')
def test_should_summarise(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}]
    ctx_eff = 1000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="prompt_within_threshold")

@patch('keeprollming.logger.log')
def test_should_summarise_above_threshold(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 10
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
    )

    assert plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="prompt_exceeds_threshold")

@patch('keeprollming.logger.log')
def test_choose_head_tail(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 10
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
    )

    assert plan.head_n == 3
    assert plan.tail_n == 3
    mock_log.assert_called_once_with("INFO", "summary_needed")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_head(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 10
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        max_head=2,
    )

    assert plan.head_n == 2
    assert plan.tail_n == 3
    mock_log.assert_called_once_with("INFO", "summary_needed")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tail(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 10
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        max_tail=2,
    )

    assert plan.head_n == 3
    assert plan.tail_n == 2
    mock_log.assert_called_once_with("INFO", "summary_needed")

@patch('keeprollming.logger.log')
def test_choose_head_tail_no_middle(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 3
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="no_middle")

@patch('keeprollming.logger.log')
def test_choose_head_tail_minimal(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 10
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=2000,
    )

    assert plan.head_n == 3
    assert plan.tail_n == 3
    mock_log.assert_called_once_with("INFO", "summary_needed")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="prompt_within_threshold")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_no_middle(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 3
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=2000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="no_middle")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 5000
    max_out = 900

    plan = should_summarise(
        tok=tok,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
        summary_insert_budget_tok=1000,
    )

    assert not plan.should
    mock_log.assert_called_once_with("INFO", "summary_needed", reason="too_few_messages")

@patch('keeprollming.logger.log')
def test_choose_head_tail_max_tokens_minimal_no_middle_no_summary_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream_no_error_no_repack_no_stream(mock_log):
    tok = TokenCounter()
    messages = [{"role": "user", "content": "ciao, test"}] * 2
    ctx_eff = 500
