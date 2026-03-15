# Keeprollming Orchestrator API Documentation

This document provides comprehensive documentation for all available endpoints in the Keeprollming orchestrator.

## Overview

The Keeprollming Orchestrator is a FastAPI-based proxy that sits in front of an OpenAI-compatible backend. It adds rolling-summary support to avoid context overflow when handling long conversations with language models.

All API requests are made using HTTP POST methods, except for streaming responses which use Server-Sent Events (SSE).

## Base URL

The base URL for all endpoints is: `http://<your-host>:8000`

## Endpoint: `/v1/chat/completions`

### Description
This endpoint handles chat completions requests and provides rolling-summary support to manage context overflow.

### Method
POST

### Request Format
```json
{
  "model": "string",
  "messages": [
    {
      "role": "user|assistant|system",
      "content": "string"
    }
  ],
  "stream": boolean,
  "max_tokens": integer,
  "temperature": number,
  "top_p": number,
  "frequency_penalty": number,
  "presence_penalty": number
}
```

### Parameters

#### Required Fields:
- `model`: String identifying the model to use. Can be:
  - Profile aliases: `"local/quick"`, `"local/main"`, `"local/deep"`
  - Passthrough mode: `"pass/<upstream_model_name>"` (e.g., `"pass/gpt-4"`)
  - Direct upstream model name (fallback)
  
#### Optional Fields:
- `messages`: Array of message objects with role and content
- `stream`: Boolean indicating if response should be streamed (SSE format)
- `max_tokens`: Maximum number of tokens for the completion
- `temperature`: Sampling temperature (0.0 to 2.0)
- `top_p`: Top-p sampling parameter (0.0 to 1.0)
- `frequency_penalty`: Frequency penalty (0.0 to 2.0)
- `presence_penalty`: Presence penalty (0.0 to 2.0)

### Response Format

#### Non-streaming Response:
```json
{
  "id": "string",
  "object": "chat.completion",
  "created": integer,
  "model": "string",
  "choices": [
    {
      "index": integer,
      "message": {
        "role": "assistant",
        "content": "string"
      },
      "finish_reason": "stop|length|function_call|tool_calls|content_filter"
    }
  ],
  "usage": {
    "prompt_tokens": integer,
    "completion_tokens": integer,
    "total_tokens": integer
  }
}
```

#### Streaming Response:
A Server-Sent Events (SSE) response with multiple `data:` events, each containing a partial completion.

### Usage Examples

#### Basic usage with local/main profile:
```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"local/main","messages":[{"role":"user","content":"ciao"}]}'
```

#### Streaming response:
```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"local/main","stream":true,"messages":[{"role":"user","content":"ciao"}]}'
```

#### Passthrough mode (direct routing):
```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"pass/gpt-4","messages":[{"role":"user","content":"ciao"}]}'
```

### Headers

The following headers are supported for caching purposes:

- `x-librechat-user-id`: User identifier for caching
- `x-librechat-conversation-id`: Conversation identifier for caching  
- `x-librechat-message-id`: Message identifier for caching
- `x-librechat-parent-message-id`: Parent message identifier for caching

### Environment Variables

The orchestrator supports the following configuration through environment variables:

#### Core Configuration:
- `UPSTREAM_BASE_URL` (default: `http://127.0.0.1:1234/v1`)
- `MAIN_MODEL`, `SUMMARY_MODEL`

#### Profile-specific Models:
- `QUICK_MAIN_MODEL`, `QUICK_SUMMARY_MODEL`
- `BASE_MAIN_MODEL`, `BASE_SUMMARY_MODEL`
- `DEEP_MAIN_MODEL`, `DEEP_SUMMARY_MODEL`

#### Summary Behavior:
- `MAX_HEAD`, `MAX_TAIL` (rolling-summary head/tail caps)
- `SUMMARY_MODE` (default: `cache_append`)
- `SUMMARY_CACHE_ENABLED` (default: `true`)
- `SUMMARY_CACHE_DIR` (default: `./__summary_cache`)

#### Context Management:
- `SAFETY_MARGIN_TOK` (default: `128`)
- `DEFAULT_CTX_LEN` (default: `4096`)

### Features

The orchestrator provides several features:

1. **Rolling Summary**: Automatically summarizes conversation history when context limits are exceeded
2. **Caching Support**: Reuses previously generated summaries for efficiency  
3. **Passthrough Mode**: Direct routing without summarization for specific models
4. **Streaming Proxy**: Supports Server-Sent Events (SSE) streaming responses
5. **Token Accounting**: Tracks and manages token usage across requests

### Profile Configurations

Available profiles with their model configurations:

- `local/quick`: 
  - Main Model: qwen2.5-3b-instruct  
  - Summary Model: qwen2.5-1.5b-instruct
- `local/main`:
  - Main Model: qwen2.5-v1-7b-instruct
  - Summary Model: qwen2.5-3b-instruct
- `local/deep`: 
  - Main Model: qwen2.5-27b-instruct  
  - Summary Model: qwen2.5-7b-instruct

### Error Handling

The orchestrator returns appropriate HTTP status codes and error messages:

- Status Code 400: Invalid payload format
- Status Code 429: Rate limiting or resource exhaustion
- Status Code 500: Internal server error during processing