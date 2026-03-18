#!/usr/bin/env python3

"""
Test script for custom summary prompt functionality.

This demonstrates how to use custom prompts in the KeepRoLLMing orchestrator.
"""

import json
import requests

# Example payload with custom summary prompt
test_payload = {
    "model": "main",
    "messages": [
        {"role": "user", "content": "What is your name?"},
        {"role": "assistant", "content": "I am a helpful AI assistant."},
        {"role": "user", "content": "Can you explain what context rolling means?"}
    ],
    "summary_prompt_type": "custom",
    "summary_prompt": """You are an expert in explaining complex concepts.
Explain the following conversation transcript:

=== TRANSCRIPT START ===
{{TRANSCRIPT}}
=== TRANSCRIPT END ===

Your explanation should be concise, technical, and clear."""
}

# Example with a named prompt type but custom content
test_payload_2 = {
    "model": "main",
    "messages": [
        {"role": "user", "content": "What is your name?"},
        {"role": "assistant", "content": "I am a helpful AI assistant."},
        {"role": "user", "content": "Can you explain what context rolling means?"}
    ],
    "summary_prompt_type": """You are an expert in explaining complex concepts.
Explain the following conversation transcript:

=== TRANSCRIPT START ===
{{TRANSCRIPT}}
=== TRANSCRIPT END ===

Your explanation should be concise, technical, and clear."""
}

print("Example 1 - Custom prompt with named type:")
print(json.dumps(test_payload, indent=2))

print("\nExample 2 - Custom prompt using the content directly as prompt_type:")
print(json.dumps(test_payload_2, indent=2))