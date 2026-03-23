# Environment & Tool Patterns

**Last Updated:** 23/03/2026

This document captures environmental quirks, LLM model behaviors, and tool-calling patterns discovered during agent sessions. These patterns help prevent repeated mistakes across sessions.

---

## Table of Contents

- [LLM Model Patterns](#llm-model-patterns)
  - [OpenAI](#openai)
- [Tool Calling Patterns](#tool-calling-patterns)
  - [HTTPX](#httpx)
- [Environment Quirks](#environment-quirks)
  - [Python Nested Scopes](#python-nested-scopes)
  - [Async/await](#asyncawait)

---

## LLM Model Patterns

### OpenAI

#### Pattern: First Chunk Role Injection
- **Trigger:** When streaming responses with tool_calls
- **Failed approach:** Send tool_calls without `role: "assistant"` in first chunk
- **Error:** OpenAI client expects `delta.role = "assistant"` in first delta
- **Workaround:** 
  - Check if first chunk is tool-only (no content)
  - If tool-only, emit synthetic role chunk first
  - Then emit tool_calls
- **Code:**
  ```python
  # Check if first chunk is tool-only
  has_tool_calls = bool(delta.get("tool_calls"))
  has_content = isinstance(delta.get("content"), str) and bool(delta.get("content"))
  
  if not role_sent:
      if has_tool_calls and not has_content:
          # Emit synthetic role chunk first
          emit({"role": "assistant"})
          role_sent = True
      else:
          delta["role"] = "assistant"
          role_sent = True
  ```
- **Why it works:** OpenAI's streaming client expects a role field in the first delta to properly initialize the message
- **Related:** `_agent/knowledge/MEMORY.md#OpenAI-Streaming-Compatibility`
- **Discovered:** 2026-03-23

---

## Tool Calling Patterns

### HTTPX

#### Pattern: Exception Handling in Nested Async Generators
- **Trigger:** When catching exceptions in nested async generator functions
- **Failed approach:** `except httpx.ConnectError:` directly in nested scope
- **Error:** `NameError: name 'httpx' is not defined`
- **Workaround:** Use runtime exception type detection with `sys.exc_info()`
- **Code:**
  ```python
  async def _iter():
      try:
          response = await client.get(url)
      except Exception as e:
          exc_type = sys.exc_info()[1].__class__
          if exc_type == httpx.ConnectError:
              handle_error()
  ```
- **Why it works:** `sys.exc_info()[1]` returns actual exception instance at runtime, bypassing static scope resolution issues
- **Related:** `_agent/knowledge/MEMORY.md#HTTPX-Exception-Handling`
- **Discovered:** 2026-03-23

---

## Environment Quirks

### Python Nested Scopes

#### Quirk: Exception Type Lookup in Nested Functions
- **Issue:** Exception types referenced in `except` clauses of nested functions may not be accessible even if imported in parent
- **Root cause:** Python resolves exception types at the scope where the `except` clause is defined, not where it executes
- **Solution:** Use runtime type checking instead of static exception types in nested scopes
- **Related:** See HTTPX pattern above for example
- **Discovered:** 2026-03-23

### Async/Await

#### Quirk: Async Generator Exception Handling
- **Issue:** Exceptions in async generators require special handling to preserve context
- **Recommended pattern:** Always wrap in `try/except Exception` and use `sys.exc_info()` for type checking
- **Why:** Async generators have different exception propagation rules than regular functions
- **Related:** `_agent/knowledge/MEMORY.md#Async-Exception-Handling`
- **Discovered:** 2026-03-23

---

## How to Add New Patterns

When you discover a new pattern:

1. **Use the CAPTURE-ENVIRONMENT-LESSON skill** to automatically save
2. **Add to appropriate section** (LLM Model, Tool Calling, or Environment)
3. **Include all details:**
   - Trigger condition
   - Failed approach
   - Error message
   - Working workaround
   - Code example
   - Explanation of why it works
4. **Link to related files** for context
5. **Date-stamp** the discovery

## Pattern Categories

- **LLM Model Patterns:** Model-specific behaviors (OpenAI, Anthropic, etc.)
- **Tool Calling Patterns:** API/tool limitations and workarounds
- **Environment Quirks:** Platform, OS, runtime, or language-specific issues

---

*This document is auto-updated by the CAPTURE-ENVIRONMENT-LESSON skill and manually reviewed during session summaries.*