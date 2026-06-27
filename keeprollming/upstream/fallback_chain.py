"""Fallback chain resolution for resilient upstream routing.

This module provides utilities for resolving and managing fallback chains
when upstream models fail, enabling automatic retries on alternative models.
"""

from typing import List, Dict, Any
import os


def resolve_fallback_chain(
    route: Dict[str, Any], 
    primary_model: str,
    req_id: str = ""
) -> List[Dict[str, str]]:
    """Resolve a fallback chain from route configuration.
    
    Extracts and formats the fallback chain from route configuration,
    excluding the primary model that has already been attempted.
    
    Args:
        route: Route configuration dictionary with fallback_chain key
        primary_model: The primary model that was first attempted
        req_id: Request ID for logging (optional)
        
    Returns:
        List of dicts with 'model' keys representing fallback targets
    """
    chain = route.get("fallback_chain", [])
    if not chain:
        return []
    
    # Filter out the primary model and any failed attempts
    visited = {primary_model}
    fallbacks = []
    
    for model in chain:
        if isinstance(model, dict):
            # Handle dict format with model key
            model_name = model.get("model", model.get("name"))
        else:
            # Handle string format
            model_name = str(model)
        
        if model_name and model_name not in visited:
            fallbacks.append({"model": model_name})
            visited.add(model_name)
    
    return fallbacks


def should_try_fallback(status_code: int, error: Exception | None = None) -> bool:
    """Determine if a response warrants trying the next fallback.
    
    Args:
        status_code: HTTP status code from the request
        error: Exception that was raised (if any)
        
    Returns:
        True if should try next fallback, False otherwise
    """
    # Always retry on connection errors
    if error is not None:
        return True
    
    # Retry on server errors (5xx) and some client errors
    if status_code >= 500:
        return True
    
    # Retry on 429 (Too Many Requests)
    if status_code == 429:
        return True
    
    return False


def format_fallback_error(status_code: int, error: Exception | None, model: str) -> str:
    """Format a fallback error message.
    
    Args:
        status_code: HTTP status code (or None if no response)
        error: Exception that was raised
        model: Model name that failed
        
    Returns:
        Formatted error message string
    """
    if error is not None:
        return f"Model {model} failed with error: {type(error).__name__}: {error}"
    
    if status_code is not None:
        return f"Model {model} returned HTTP {status_code}"
    
    return f"Model {model} failed with unknown error"
