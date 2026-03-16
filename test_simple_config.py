#!/usr/bin/env python3

from keeprollming.config import CONFIG
import keeprollming.rolling_summary as rs

print("Testing custom prompts in config:")

# Print what we have loaded from the config file
print(f"Custom prompts key exists: {'custom_summary_prompts' in CONFIG}")
if 'custom_summary_prompts' in CONFIG:
    print(f"Value: {repr(CONFIG['custom_summary_prompts'])}")

# Test our new function with direct text prompt 
result = rs.load_summary_prompt_template("test_direct")
print(f"Direct test result length: {len(result)}")

# Test default prompt
result2 = rs.load_summary_prompt_template("classic")  
print(f"Classic test result length: {len(result2)}")