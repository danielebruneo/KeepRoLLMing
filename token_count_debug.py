#!/usr/bin/env python3

import sys
sys.path.insert(0, '/home/daniele/LLM/orchestrator')

from keeprollming.token_counter import TokenCounter
from keeprollming.rolling_summary import _estimate_tokens_for_msgs

# Replicate exact scenario from test with 50x multiplier 
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
for i in range(10):
    messages.append({"role": "user", "content": f"msg {i} - " + long_text_content})
    messages.append({"role": "assistant", "content": f"reply {i} - " + ("This is a response with tokens." * 8)})
messages.append({"role": "user", "content": "domanda finale cache"})

print(f"Number of messages: {len(messages)}")

tok = TokenCounter()
est_tokens = _estimate_tokens_for_msgs(tok, messages)
print(f"Estimated tokens for all messages: {est_tokens}")

# Check context values from test setup
ctx_eff = 260  # From test config 
max_out = 256  # Calculated via _clamp_max_out_for_ctx

threshold = max(256, int(ctx_eff) - int(max_out) - int(128))
print(f"Threshold: {threshold}")
print(f"Should summarize? {est_tokens > threshold}")

# Check how many characters we have
total_chars = sum(len(msg['content']) for msg in messages)
print(f"Total characters: {total_chars}")