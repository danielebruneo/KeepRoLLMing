import pytest
from keeprollming.app import should_summarise, choose_head_tail
from unittest.mock import MagicMock

@pytest.mark.parametrize("messages, ctx_eff, max_out, summary_insert_budget_tok, expected", [
    ([{"role": "user", "content": "ciao, test"}] * 2, 5000, 900, 1000, False),
    ([{"role": "user", "content": "ciao, test"}] * 3, 5000, 900, 1000, True),
])
def test_should_summarise(messages, ctx_eff, max_out, summary_insert_budget_tok, expected):
    tok = MagicMock()
    plan = should_summarise(tok=tok, messages=messages, ctx_eff=ctx_eff, max_out=max_out, summary_insert_budget_tok=summary_insert_budget_tok)
    assert plan.should == expected

@pytest.mark.parametrize("messages, ctx_eff, max_out, summary_insert_budget_tok, expected", [
    ([{"role": "user", "content": "ciao, test"}] * 2, 5000, 900, 1000, (0, 3)),
    ([{"role": "user", "content": "ciao, test"}] * 3, 5000, 900, 1000, (2, 1)),
])
def test_choose_head_tail(messages, ctx_eff, max_out, summary_insert_budget_tok, expected):
    tok = MagicMock()
    result = choose_head_tail(tok=tok, messages=messages, ctx_eff=ctx_eff, max_out=max_out, summary_insert_budget_tok=summary_insert_budget_tok)
    assert result == expected

def test_env_overrides(monkeypatch):
    monkeypatch.setenv("SUMMARY_THRESHOLD", "10")
    tok = MagicMock()
    plan = should_summarise(tok=tok, messages=[{"role": "user", "content": "ciao, test"}] * 2, ctx_eff=5000, max_out=900, summary_insert_budget_tok=1000)
    assert plan.should == True
