"""Message processing utilities.

This module provides utilities for message validation, repacking, and context
length management that are used throughout the request processing pipeline.
"""

from typing import Any, Dict, List, Optional, Tuple


def validate_messages(messages: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """Validate a list of messages for basic correctness.
    
    Args:
        messages: List of message dictionaries to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not messages:
        return False, "No messages provided"
    
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            return False, f"Message {i} is not a dictionary"
        
        if "role" not in msg:
            return False, f"Message {i} missing 'role' field"
        
        if "content" not in msg:
            return False, f"Message {i} missing 'content' field"
    
    # Check for at least one user message
    has_user = any(msg.get("role") == "user" for msg in messages)
    if not has_user:
        return False, "No user message found in conversation"
    
    return True, ""


def extract_messages_from_request(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract messages array from a chat completion request payload.
    
    Args:
        payload: Full request payload dict
        
    Returns:
        List of message dictionaries
    """
    return payload.get("messages", [])


def count_messages(messages: List[Dict[str, Any]]) -> int:
    """Count the number of messages in a conversation.
    
    Args:
        messages: List of message dictionaries
        
    Returns:
        Number of messages
    """
    return len(messages)


def split_messages_by_role(messages: List[Dict[str, Any]]) -> Tuple[List[Dict], List[Dict]]:
    """Split messages into system and non-system messages.
    
    Args:
        messages: List of message dictionaries
        
    Returns:
        Tuple of (system_messages, non_system_messages)
    """
    system = []
    non_system = []
    
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "system":
            system.append(msg)
        else:
            non_system.append(msg)
    
    return system, non_system


def ensure_user_message_present(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure the message list contains at least one user message.
    
    This is important for models that require a user message as the last turn.
    
    Args:
        messages: List of message dictionaries
        
    Returns:
        Message list with user message added if necessary
    """
    has_user = any(msg.get("role") == "user" for msg in messages)
    if has_user:
        return messages
    
    # Add a placeholder user message if none exists
    return messages + [{"role": "user", "content": ""}]


def truncate_messages_to_context(
    messages: List[Dict[str, Any]], 
    max_tokens: int, 
    token_counter
) -> List[Dict[str, Any]]:
    """Truncate messages to fit within context window.
    
    This is a simple truncation strategy that removes oldest messages until
    the total fits within the context limit. Note: For production use,
    consider using summarization instead of truncation.
    
    Args:
        messages: List of message dictionaries
        max_tokens: Maximum tokens allowed
        token_counter: TokenCounter instance for counting
        
    Returns:
        Truncated list of messages that fit within context window
    """
    # Keep removing oldest non-system messages until we fit
    while messages and token_counter.count_messages(messages) > max_tokens:
        # Find first non-system message
        for i, msg in enumerate(messages):
            if msg.get("role") != "system":
                messages = messages[:i] + messages[i+1:]
                break
        else:
            # All messages are system messages - return as-is
            break
    
    return messages


def calculate_token_efficiency(
    original_tokens: int, 
    final_tokens: int
) -> float:
    """Calculate the token efficiency of a compression operation.
    
    Args:
        original_tokens: Original token count before compression
        final_tokens: Token count after compression
        
    Returns:
        Efficiency ratio (final / original), where < 1 means compression
    """
    if original_tokens == 0:
        return 1.0
    return final_tokens / original_tokens


def find_last_n_messages(
    messages: List[Dict[str, Any]], 
    n: int
) -> List[Dict[str, Any]]:
    """Get the last N messages from a conversation.
    
    Args:
        messages: List of message dictionaries
        n: Number of messages to retrieve
        
    Returns:
        Last n messages (or all if fewer than n exist)
    """
    return messages[-n:] if len(messages) >= n else messages
