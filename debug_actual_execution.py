#!/usr/bin/env python3

"""
Debug the exact execution path of the failing test with detailed logging
"""

import os
from pathlib import Path
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

os.environ['LOG_MODE'] = 'DEBUG'
os.environ['SUMMARY_CACHE_ENABLED'] = 'True'

def main():
    print("=== Debug Actual Execution ===")
    
    # Import all needed modules
    from keeprollming.config import (
        BASE_MAIN_MODEL,
        BASE_SUMMARY_MODEL,
        DEFAULT_CTX_LEN,
        MAIN_MODEL,
        SUMMARY_MODEL,
        CONFIG
    )
    from keeprollming.token_counter import TokenCounter
    from keeprollming.rolling_summary import should_summarise
    
    TOK = TokenCounter()
    
    print("Configuration loaded:")
    print(f"  Main model: {MAIN_MODEL}")
    print(f"  Summary model: {SUMMARY_MODEL}")
    print(f"  Default context length: {DEFAULT_CTX_LEN}")
    print(f"  Summary cache enabled: {CONFIG.get('summary_cache_enabled')}")
    
    # Replicate the exact test message creation
    long_text_content = (
        "This is a sample text with several meaningful words that will consume many tokens. "
        "Each sentence contains multiple words and the overall length should be sufficient to "
        "trigger the summary overflow mechanism when combined with other messages in conversation. "
        "The text is designed to accurately represent token usage rather than character count. "
        "This repetition ensures we get proper token-based triggering of summarization. "
        "We are testing that context overflow works properly by creating a sufficiently long conversation. "
        "Multiple sentences and words will ensure the appropriate number of tokens accumulate. "
    ) * 50

    messages = []

    # Create 10 user/assistant pairs with long content
    for i in range(10):
        messages.append({"role": "user", "content": f"msg {i} - " + long_text_content})
        messages.append({"role": "assistant", "content": f"reply {i} - " + ("This is a response with tokens." * 8)})
    messages.append({"role": "user", "content": "domanda finale cache"})
    
    print(f"\nCreated {len(messages)} messages")
    
    # Calculate token count
    total_tokens = TOK.count_messages(messages)
    print(f"Total estimated tokens: {total_tokens}")
    
    # Test with context length of 260 (from test configuration)
    ctx_eff = 260  # From the fake backend config in test setup
    max_out = 64
    
    print(f"\nContext effective length: {ctx_eff}")
    print(f"Max output tokens: {max_out}")
    
    threshold = max(256, int(ctx_eff) - int(max_out) - 256)
    print(f"Threshold calculation: max(256, {ctx_eff} - {max_out} - 256) = {threshold}")
    
    # Now run should_summarise
    plan = should_summarise(
        tok=TOK,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
    )
    
    print(f"\nSummary decision:")
    print(f"  Should summarize: {plan.should}")
    print(f"  Reason: {plan.reason}")
    print(f"  Prompt token estimate: {plan.prompt_tok_est}")
    print(f"  Threshold: {plan.threshold}")
    print(f"  Head_n: {plan.head_n}")
    print(f"  Tail_n: {plan.tail_n}")
    print(f"  Middle count: {plan.middle_count}")
    
    # Let's also check what we'd expect from the fake backend setup:
    print("\nFake backend config (from test):")
    print("  models.main-model.context_length: 260")
    print("  summary.overflow_if_prompt_chars_gt: 2600")

if __name__ == "__main__":
    main()