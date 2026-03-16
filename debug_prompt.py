#!/usr/bin/env python3

import keeprollming.config as cfg
from keeprollming.rolling_summary import load_summary_prompt_template, CONFIG

# Let's debug what config is loaded 
print("CONFIG in rolling_summary.py:", CONFIG)

# Check the custom_summary_prompts specifically:
custom_prompts = CONFIG.get('custom_summary_prompts', {})
print("Custom prompts from CONFIG:", custom_prompts)

# Let's examine directly with actual code execution
print("\nTesting our function:")
result = load_summary_prompt_template("direct_prompt")
print(f"Result: {repr(result)}")

# Try to see what happens in the existing test cases 
print("\nTesting default prompt types:")
classic_result = load_summary_prompt_template("classic")
print(f"Classic result length: {len(classic_result)}")

curated_result = load_summary_prompt_template("curated")
print(f"Curated result length: {len(curated_result)}")