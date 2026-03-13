#!/usr/bin/env python3

"""
Debug test for the failing cache hit test to capture detailed logs and understand
the summary plan being generated.
"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

print("=== Debug Cache Hit Test ===")

# Set logging mode to DEBUG for maximum verbosity
os.environ['LOG_MODE'] = 'DEBUG'

try:
    # Import required modules
    from keeprollming.config import CONFIG
    from keeprollming.logger import log
    
    print(f"Configuration loaded:")
    print(f"  summary_cache_enabled: {CONFIG.get('summary_cache_enabled')}")
    print(f"  summary_cache_dir: {CONFIG.get('summary_cache_dir')}")
    
    # Test the same scenario as in test_e2e_summary_cache_hit_reuses_previous_summary
    print("\nTesting cache hit scenario...")
    
    # Simulate the messages that would trigger summarization  
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
    
    # Print message info to understand the content
    print(f"\nCreated {len(messages)} messages")
    total_chars = sum(len(msg['content']) for msg in messages if 'content' in msg)
    print(f"Total character count: {total_chars}")
    
    # Log what would happen with these messages
    log("DEBUG", "cache_test_message_analysis",
        message_count=len(messages),
        total_chars=total_chars,
        last_user_content_len=len(messages[-1]['content']),
        first_user_content_len=len(messages[0]['content'])
    )
    
    print("\nTest completed successfully - logs captured")
    
except Exception as e:
    print(f"Error during test: {e}")
    import traceback
    traceback.print_exc()

print("=== Debug complete ===")