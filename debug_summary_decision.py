#!/usr/bin/env python3

"""
Debug the summary decision-making process to understand why no summaries are being triggered.
"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

print("=== Debug Summary Decision Process ===")

os.environ['LOG_MODE'] = 'DEBUG'

try:
    # Import modules needed for analysis  
    from keeprollming.config import (
        BASE_MAIN_MODEL,
        BASE_SUMMARY_MODEL,
        DEFAULT_CTX_LEN,
        DEEP_MAIN_MODEL,
        DEEP_SUMMARY_MODEL,
        MAIN_MODEL,
        QUICK_MAIN_MODEL,
        QUICK_SUMMARY_MODEL,
        SUMMARY_MODEL,
        UPSTREAM_BASE_URL,
        CONFIG
    )
    
    from keeprollming.logger import log
    
    print("Configuration loaded:")
    print(f"  Main model: {MAIN_MODEL}")
    print(f"  Summary model: {SUMMARY_MODEL}")
    print(f"  Default context length: {DEFAULT_CTX_LEN}")
    print(f"  Summary cache enabled: {CONFIG.get('summary_cache_enabled')}")
    
    # Let's simulate the logic that determines if summarization is needed
    # The test creates a specific scenario - let's analyze what happens
    
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
    
    # Log detailed message analysis 
    total_chars = sum(len(msg['content']) for msg in messages if 'content' in msg)
    print(f"\nMessages created:")
    print(f"  Total messages: {len(messages)}")
    print(f"  Total character count: {total_chars}")
    
    log("DEBUG", "summary_decision_analysis",
        message_count=len(messages),
        total_chars=total_chars,
        first_message_len=len(messages[0]['content']),
        last_message_len=len(messages[-1]['content']),
        role_distribution=list({msg['role'] for msg in messages if 'role' in msg}),
        # Try to calculate approximate token usage
        estimated_tokens=int(total_chars * 0.5)  # rough approximation 
    )
    
    print("\nThis test is designed to trigger summary overflow")
    print("If it's not triggering, the issue might be with:")
    print("- The character threshold (2600 in config)")
    print("- Token estimation logic")
    print("- The message structure being processed")
    
except Exception as e:
    print(f"Error during test: {e}")
    import traceback
    traceback.print_exc()

print("=== Debug summary decision complete ===")