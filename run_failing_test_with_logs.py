#!/usr/bin/env python3

"""
Run the failing test with maximum debug logging and detailed analysis.
"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

print("=== Running Failing Test With Detailed Logging ===")

# Set environment for maximum logging verbosity
os.environ['LOG_MODE'] = 'DEBUG'
os.environ['LOG_PAYLOAD_MAX_CHARS'] = '10000000'  # Increase payload log size

try:
    from tests.e2e.test_http_e2e import test_e2e_summary_cache_hit_reuses_previous_summary
    
    print("Test function imported successfully")
    
    # We'll create a minimal version that just shows the exact scenario without 
    # running all the pytest framework setup
    print("\nAnalyzing the test parameters...")
    
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
    print(f"Test scenario created:")
    print(f"  Total messages: {len(messages)}")
    print(f"  Total characters: {total_chars}")
    
    # Log this scenario for analysis
    from keeprollming.logger import log
    log("DEBUG", "failing_test_scenario",
        message_count=len(messages),
        total_chars=total_chars,
        first_message_len=len(messages[0]['content']),
        last_message_len=len(messages[-1]['content'])
    )
    
    print("\nTest scenario analysis completed.")
    print("The test should demonstrate:")
    print("- Messages that exceed the 2600 character threshold for summary triggering")  
    print("- Cache save operation")
    print("- Both requests completing successfully with cache reuse")
    
except Exception as e:
    print(f"Error during setup: {e}")
    import traceback
    traceback.print_exc()

print("=== Setup complete ===")