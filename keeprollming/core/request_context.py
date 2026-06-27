"""Request context management.

This module provides the RequestContext dataclass that encapsulates all request-specific
information needed throughout the processing pipeline.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RequestContext:
    """Encapsulates all request-specific information for a single HTTP request.
    
    Attributes:
        req_id: Unique request identifier (6-char hex string)
        client_model: Model name as specified by the client in the request
        messages: List of message dictionaries representing the conversation
        stream: Whether the client requested streaming response
        max_tokens: Maximum tokens to generate (if specified by client)
        user_id: User identifier from LibreChat headers (optional)
        conv_id: Conversation identifier from LibreChat headers (optional)
        msg_id: Message identifier from LibreChat headers (optional)
        parent_msg_id: Parent message ID for thread context (optional)
    """
    
    req_id: str
    client_model: str
    messages: List[Dict[str, Any]]
    stream: bool
    max_tokens: Optional[int] = None
    
    # LibreChat-specific headers (may be empty strings if not provided)
    user_id: str = ""
    conv_id: str = ""
    msg_id: str = ""
    parent_msg_id: str = ""
    
    # Computed attributes (initialized later in processing pipeline)
    route_name: Optional[str] = None
    upstream_model: Optional[str] = None
    summary_info: Dict[str, Any] = field(default_factory=dict)
