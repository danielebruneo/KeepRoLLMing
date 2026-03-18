"""
Test suite for custom summary prompts configuration functionality
"""

import pytest
from unittest.mock import patch, MagicMock
import tempfile
import os

from keeprollming.rolling_summary import load_summary_prompt_template


def test_load_default_prompt():
    """Test that default prompt templates still work as before"""
    # This should return one of the default templates
    result = load_summary_prompt_template("classic")
    assert isinstance(result, str)
    assert "Sei un assistente" in result or "You are a context compaction engine" in result


def test_load_custom_prompt_from_file():
    """Test loading custom prompts from file paths defined in config.yaml"""

    # Create temporary prompt files
    with tempfile.TemporaryDirectory() as tmpdir:
        prompt_content = "Custom prompt template content"

        # Write to a file that would be loaded by our system
        test_prompt_path = os.path.join(tmpdir, "_prompts", "test_custom.txt")
        os.makedirs(os.path.dirname(test_prompt_path), exist_ok=True)
        with open(test_prompt_path, 'w') as f:
            f.write(prompt_content)

        # Mock both SUMMARY_PROMPT_DIR and CONFIG to point to our temporary directory
        mock_config = {
            "custom_summary_prompts": {
                "test_custom": test_prompt_path
            }
        }
        
        with patch('keeprollming.rolling_summary.SUMMARY_PROMPT_DIR', tmpdir):
            with patch('keeprollming.rolling_summary.CONFIG', mock_config):
                result = load_summary_prompt_template("test_custom")
                assert result == prompt_content


def test_load_direct_text_from_config():
    """Test that direct text in config.yaml works"""
    
    # Mock CONFIG to include a custom prompt as direct text
    mock_config = {
        "custom_summary_prompts": {
            "direct_template": "Direct text prompt content here"
        }
    }
    
    with patch('keeprollming.rolling_summary.CONFIG', mock_config):
        result = load_summary_prompt_template("direct_template")
        assert result == "Direct text prompt content here"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])