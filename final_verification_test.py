#!/usr/bin/env python3

"""
Final verification that our custom prompt functionality is working correctly.
"""

import tempfile
import os
from pathlib import Path
import yaml
from unittest.mock import patch, mock_open
import keeprollming.rolling_summary as rs


def test_load_custom_prompt_from_config():
    """Test the new capability to load direct text from config"""
    
    # Create a temporary directory structure for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        # Mock the prompt file content that would be in _prompts/direct_text.txt
        mock_file_content = "This is custom prompt text directly defined in configuration"
        
        # Test that we can load from config correctly (simulating what our implementation does)
        with patch('keeprollming.rolling_summary.CONFIG', {
            'custom_summary_prompts': {
                'direct_prompt': mock_file_content
            }
        }):
            result = rs.load_summary_prompt_template("direct_prompt")
            
        # Should return the direct text content as configured (not a file path or default)
        assert result == mock_file_content, f"Expected '{mock_file_content}', got '{result}'"
        print("✓ Direct prompt from config loaded successfully")


def test_load_default_prompt():
    """Test that default fallback still works"""
    
    with patch('keeprollming.rolling_summary.CONFIG', {
            'custom_summary_prompts': {}
        }):
        result = rs.load_summary_prompt_template("classic")
        
    # Should return a non-empty string
    assert len(result) > 0
    print("✓ Default prompt loaded successfully")


def test_load_file_prompt():
    """Test that we can load from file"""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create _prompts directory and a sample prompt file
        prompts_dir = os.path.join(tmpdir, "_prompts")
        os.makedirs(prompts_dir)
        
        prompt_path = os.path.join(prompts_dir, "test_file_prompt.txt")
        content = "This is loaded from file"
        with open(prompt_path, 'w') as f:
            f.write(content)
            
        # Test that we can load from a file path
        with patch('keeprollming.rolling_summary.SUMMARY_PROMPT_DIR', prompts_dir):
            with patch('keeprollming.rolling_summary.CONFIG', {
                'custom_summary_prompts': {
                    'test_file_prompt': "./test_file_prompt.txt"
                }
            }):
                result = rs.load_summary_prompt_template("test_file_prompt")
                
        # Should return the file content
        assert result == content, f"Expected '{content}', got '{result}'"
        print("✓ File-based prompt loaded successfully")


if __name__ == "__main__":
    test_load_custom_prompt_from_config()
    test_load_default_prompt() 
    test_load_file_prompt()
    print("\nAll tests passed! Implementation working correctly.")