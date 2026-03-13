#!/usr/bin/env python3

"""
Final analysis of why cache hit test fails.
"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

print("=== Final Analysis: Why Cache Hit Test Fails ===")

os.environ['LOG_MODE'] = 'DEBUG'

try:
    # Import all relevant modules
    from keeprollming.config import (
        DEFAULT_CTX_LEN,
        SAFETY_MARGIN_TOK,
        SUMMARY_MAX_TOKENS,
    )
    
    from keeprollming.logger import log
    
    print("=== Configuration Analysis ===")
    print(f"DEFAULT_CTX_LEN: {DEFAULT_CTX_LEN}")
    print(f"SAFETY_MARGIN_TOK: {SAFETY_MARGIN_TOK}") 
    print(f"SUMMARY_MAX_TOKENS: {SUMMARY_MAX_TOKENS}")
    
    # The test scenario
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
    
    # Create exactly same scenario as in failing test
    for i in range(10):
        messages.append({"role": "user", "content": f"msg {i} - " + long_text_content})
        messages.append({"role": "assistant", "content": f"reply {i} - " + ("This is a response with tokens." * 8)})
    messages.append({"role": "user", "content": "domanda finale cache"})
    
    total_chars = sum(len(msg['content']) for msg in messages if 'content' in msg)
    print(f"\n=== Test Scenario Analysis ===")
    print(f"Total messages: {len(messages)}")
    print(f"Total characters: {total_chars}")
    print(f"First message length: {len(messages[0]['content'])}")
    print(f"Last message length: {len(messages[-1]['content'])}")
    
    # What we expect to see in the fake backend logic:
    # 1. The test sets "overflow_if_prompt_chars_gt": 2600 
    # 2. This means that if total text > 2600, it should return error
    # BUT this is NOT what determines summarization - it's for testing overflow errors
    
    print(f"\n=== Key Insight ===")
    print("The fake backend error logic uses 'overflow_if_prompt_chars_gt' to determine when to return an error.")
    print("But the actual decision about whether to summarize happens in should_summarise() function.")
    
    # From our earlier analysis:
    ctx_eff = DEFAULT_CTX_LEN  # Usually 4096
    max_out = 64  # from test payload
    
    threshold = max(256, int(ctx_eff) - int(max_out) - int(SAFETY_MARGIN_TOK))
    print(f"\n=== Summary Decision Logic ===")
    print(f"Context effective: {ctx_eff}")
    print(f"Max output tokens (from payload): {max_out}") 
    print(f"Safety margin: {SAFETY_MARGIN_TOK}")
    print(f"Calculated threshold: {threshold}")
    
    # The test says it should create a scenario where messages exceed the 2600 char limit
    # BUT the summarization decision is based on tokens vs context, not characters
    
    estimated_tokens = int(total_chars * 0.5)  # rough approximation 
    print(f"Estimated prompt tokens: {estimated_tokens}")
    
    if estimated_tokens > threshold:
        print("✓ Summary should be triggered (tokens exceed threshold)")
        log("INFO", "summary_decision_analysis",
            scenario="cache_hit_test",
            expected_summary_trigger=True,
            estimated_tokens=estimated_tokens,
            threshold=threshold
        )
    else:
        print("✗ Summary NOT triggered (tokens below threshold)")  
        log("INFO", "summary_decision_analysis",
            scenario="cache_hit_test",
            expected_summary_trigger=False,
            estimated_tokens=estimated_tokens,
            threshold=threshold
        )
        
    print("\n=== Conclusion ===")
    print("The test fails because:")
    print("- The messages create ~153K tokens")
    print("- Threshold calculated is ~3,904 tokens") 
    print("- Should trigger summarization (153K > 3.9K)")
    print("- BUT the cache hit test only passes when BOTH requests happen")
    print("- The first request should make a summary call and save to cache")
    print("- Second request should reuse that cached summary")
    
    # The most likely issue: 
    # In the actual test execution, there's some difference in how token counting works
    # or other condition prevents summarization
    
except Exception as e:
    print(f"Error during analysis: {e}")
    import traceback
    traceback.print_exc()

print("=== Final analysis complete ===")