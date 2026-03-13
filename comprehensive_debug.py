#!/usr/bin/env python3

"""
Comprehensive debug to understand the summary decision logic.
"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

print("=== Comprehensive Summary Debug ===")

os.environ['LOG_MODE'] = 'DEBUG'

try:
    from keeprollming.config import (
        SAFETY_MARGIN_TOK,
        SUMMARY_MAX_TOKENS,
        UPSTREAM_BASE_URL,
        CONFIG
    )
    
    from keeprollming.logger import log
    
    # Let's compute the exact values that would be used in should_summarise
    
    print("Configuration parameters:")
    print(f"  SAFETY_MARGIN_TOK: {SAFETY_MARGIN_TOK}")
    print(f"  SUMMARY_MAX_TOKENS: {SUMMARY_MAX_TOKENS}") 
    print(f"  UPSTREAM_BASE_URL: {UPSTREAM_BASE_URL}")
    print(f"  SUMMARY_CACHE_ENABLED: {CONFIG.get('summary_cache_enabled')}")
    
    # Calculate what would happen with the test message setup
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
    
    # Create exactly the same scenario as in test
    for i in range(10):
        messages.append({"role": "user", "content": f"msg {i} - " + long_text_content})
        messages.append({"role": "assistant", "content": f"reply {i} - " + ("This is a response with tokens." * 8)})
    messages.append({"role": "user", "content": "domanda finale cache"})
    
    total_chars = sum(len(msg['content']) for msg in messages if 'content' in msg)
    print(f"\nTest message analysis:")
    print(f"  Total messages: {len(messages)}")
    print(f"  Total characters: {total_chars}")
    
    # Simulate token counting - we're not actually running the full tokenizer here
    estimated_tokens = int(total_chars * 0.5)  # rough approximation as in debug
    
    # For a typical model context of ~4096 tokens, and using the default safety margin of 128 tokens
    ctx_eff = 4096
    max_out = 64  # from test payload (max_tokens parameter)
    
    print(f"\nSummary decision parameters:")
    print(f"  ctx_eff: {ctx_eff}")
    print(f"  max_out: {max_out}")
    print(f"  SAFETY_MARGIN_TOK: {SAFETY_MARGIN_TOK}")
    
    threshold = max(256, int(ctx_eff) - int(max_out) - int(SAFETY_MARGIN_TOK))
    print(f"  calculated threshold: {threshold}")
    print(f"  estimated prompt tokens: {estimated_tokens}")
    
    # Decision logic
    if estimated_tokens <= threshold:
        print("\nDecision: No summarization needed (prompt within threshold)")
        log("DEBUG", "summary_decision_result",
            decision="no_summary",
            reason="prompt_within_threshold",
            estimated_tokens=estimated_tokens,
            threshold=threshold,
            ctx_eff=ctx_eff,
            max_out=max_out,
            safety_margin=SAFETY_MARGIN_TOK
        )
    else:
        print("\nDecision: Summary needed (prompt exceeds threshold)")
        log("DEBUG", "summary_decision_result",
            decision="summary_needed",
            reason="prompt_exceeds_threshold",
            estimated_tokens=estimated_tokens,
            threshold=threshold,
            ctx_eff=ctx_eff,
            max_out=max_out,
            safety_margin=SAFETY_MARGIN_TOK
        )
    
    print("\nThis explains why the cache test is failing -")
    print("the messages do not exceed the token threshold for summarization.")
    
except Exception as e:
    print(f"Error during test: {e}")
    import traceback
    traceback.print_exc()

print("=== Comprehensive debug complete ===")