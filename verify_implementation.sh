#!/bin/bash

# Simple verification that our changes work correctly
echo "=== FINAL VERIFICATION ==="

echo "1. Checking config.yaml has the new custom_summary_prompts section:"
grep -A 5 "custom_summary_prompts:" /home/daniele/LLM/orchestrator/config.yaml

echo -e "\n2. Testing loading of direct text prompt from config with Python:"
cd /home/daniele/LLM/orchestrator
python3 -c "
import yaml
from keeprollming.config import CONFIG

# Load and parse the config like our system does 
config = yaml.safe_load(open('config.yaml'))
print(f'Custom prompts in YAML file: {repr(config.get(\"custom_summary_prompts\", {}))}')

# Verify what's actually loaded by our config module  
print(f'Custom prompts from CONFIG: {repr(CONFIG.get(\"custom_summary_prompts\", {}))}')
"

echo -e "\n3. Running tests for core functionality:"
python3 -m pytest tests/test_custom_prompts_logic.py -v