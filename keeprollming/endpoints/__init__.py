"""Endpoints module - API route handlers.

This module provides:
- Chat completions endpoint handler (/v1/chat/completions)
- Embeddings endpoint handler (/v1/embeddings)
- Request/response orchestration
"""

from .chat_completions import (
    process_chat_request,
)
from .embeddings import (
    embeddings_handler,
    process_embedding_request,
)

__all__ = [
    "process_chat_request",
    "embeddings_handler",
    "process_embedding_request",
]
