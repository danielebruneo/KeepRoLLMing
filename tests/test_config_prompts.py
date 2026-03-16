"""
Test suite for custom summary prompts from config functionality
"""

import pytest
from unittest.mock import patch
import tempfile
import os

# Import our function directly to test it
import keeprollming.rolling_summary as rs


def test_custom_prompt_from_file():
    """Test loading a prompt file through CONFIG with relative path"""
    
    # Create temporary directory structure and a test file  
    with tempfile.TemporaryDirectory() as tmpdir:
        prompt_content = "Custom prompt template content for testing"
        
        # Write to a file that would be loaded by our system
        test_prompt_path = os.path.join(tmpdir, "_prompts", "test_custom.txt")
        os.makedirs(os.path.dirname(test_prompt_path), exist_ok=True)
        with open(test_prompt_path, 'w') as f:
            f.write(prompt_content)
            
        # Mock the config to include a custom prompt
        mock_config = {
            "custom_summary_prompts": {
                "test_custom_prompt": "./_prompts/test_custom.txt"
            }
        }
        
        # Mock both the SUMMARY_PROMPT_DIR and CONFIG 
        with patch('keeprollming.rolling_summary.SUMMARY_PROMPT_DIR', tmpdir):
            with patch('keeprollming.rolling_summary.CONFIG', mock_config):
                result = rs.load_summary_prompt_template("test_custom_prompt")
                
        assert result == prompt_content


def test_custom_prompt_direct_text():
    """Test loading custom prompt text directly from config"""

    # Mock the config to include a direct text prompt
    mock_config = {
        "custom_summary_prompts": {
            "structured_explainer": "Direct text content for the prompt template"
        }
    }

    with patch('keeprollming.rolling_summary.CONFIG', mock_config):
        result = rs.load_summary_prompt_template("structured_explainer")

    assert result == "Direct text content for the prompt template"


def test_default_fallback():
    """Test that default prompts still work when custom isn't specified"""
    
    # Mock empty config to ensure fallback behavior
    mock_config = {
        "custom_summary_prompts": {}
    }
        
    with patch('keeprollming.rolling_summary.CONFIG', mock_config):
        result = rs.load_summary_prompt_template("classic")
        
    assert len(result) > 0
    assert "Sei un assistente" in result or "You are a context compaction engine" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])