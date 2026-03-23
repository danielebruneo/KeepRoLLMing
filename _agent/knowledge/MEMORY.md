# Project Memory - CATALYST Orchestrator

## Task Tracking
- Current active task: Fix HTTPX exception handling in nested async generator
- Focus areas: Exception handling, streaming compatibility, logging

## Implementation Patterns

### HTTPX Exception Handling in Nested Generators
- Use `sys.exc_info()[1].__class__` for runtime exception type detection
- Avoid static `except httpx.ConnectError:` in nested scopes

### OpenAI Streaming Compatibility
- Inject `role: "assistant"` in first delta
- Handle tool-only chunks with synthetic role preface
- Merge tool_calls arguments chunk-by-chunk

### Logging Configuration
- `MAX_BODY_CHARS=10_000_000` for full response capture
- `MAX_SSE_BYTES=10_000_000` for full SSE stream capture
- `LOG_SNIP_CHARS=0` to disable truncation

## Known Issues & Solutions

### Issue: NameError in nested exception handlers
**Solution:** Use `sys.exc_info()[1].__class__` to get exception type at runtime

### Issue: Missing role in reconstructed response logs
**Solution:** Ensure role injection happens in both streaming output AND logged reconstruction

## Configuration
- LOG_LEVEL="DEBUG" for verbose logging
- ENABLE_OPENAI_STREAM_COMPAT=1 for OpenAI compatibility mode

---

# Lessons Learned - HTTPX Exception Handling & OpenAI Streaming Compatibility

## Date: 2026-03-23

### Problem: NameError in Nested Async Generator Exception Handlers

**Issue:** When catching `httpx` exceptions inside a nested async generator (`_iter` function), using `except httpx.ConnectError:` directly fails with `NameError: name 'httpx' is not defined`.

**Root Cause:** In Python's exception handling, exception types must be resolved at the scope where the `except` clause is defined, not where it executes. When the exception handler is in a nested function, the exception type lookup can fail if the import is not properly accessible in that nested scope.

**Solution:** Use runtime exception type detection:
```python
except Exception as e:
    exc_type = sys.exc_info()[1].__class__
    if exc_type == httpx.ConnectError:
        # Handle connection error
```

**Why This Works:** `sys.exc_info()[1]` returns the actual exception instance at runtime, and `.__class__` gives us the dynamic type. This bypasses the static scope resolution issue.

**Lesson:** When dealing with nested exception handlers, prefer runtime type checking over static type references in the `except` clause.

---

## Problem: OpenAI-Compatible Streaming with Tool Calls

**Issue:** OpenAI's streaming API requires `delta.role = "assistant"` to be present in the first chunk. When the upstream model sends tool_calls without content in the first chunk, clients expect this role field.

**Current Working Pattern:**
1. **Check if first chunk is tool-only:**
   ```python
   has_tool_calls_emit = bool(delta_emit.get("tool_calls"))
   has_content_emit = isinstance(delta_emit.get("content"), str) and bool(delta_emit.get("content"))
   
   if not role_sent:
       if has_tool_calls_emit and not has_content_emit:
           # Emit synthetic role chunk first
           emit_role_preface = True
       else:
           delta_emit["role"] = "assistant"
           role_sent = True
   ```

2. **Emit synthetic role chunk if needed:**
   ```python
   if emit_role_preface:
       role_chunk_obj = {
           "choices": [{"index": 0, "delta": {"role": "assistant"}}]
       }
       yield role_chunk_obj
       role_sent = True
   ```

3. **Add role to actual chunk:**
   ```python
   delta_emit["role"] = delta_emit.get("role") or "assistant"
   ```

**Lesson:** For OpenAI compatibility, inject `role: "assistant"` in the first delta, but handle tool-only chunks by emitting a separate role-preface chunk.

---

## Problem: Role Not Appearing in Reconstructed Response Logs

**Issue:** The `response_body` logged in `response_stream_reconstructed` and `response_sent_downstream` was missing `role: "assistant"` even though it was correctly injected into the streamed output.

**Root Cause Analysis:**
1. Role injection happened during streaming (for downstream clients)
2. `reconstructed_response` was built from upstream chunks (which don't have injected role)
3. Final check for role in delta was **inside** the `if final_tool_calls:` block
4. When tool_calls existed, the block ran, but the role check only ran if delta didn't already have role
5. Since delta existed from earlier chunks, the role check was skipped

**The Bug:**
```python
if final_tool_calls:  # Role check is INSIDE this conditional
    ...
    if "role" not in reconstructed_response["choices"][0]["delta"]:
        reconstructed_response["choices"][0]["delta"]["role"] = "assistant"
```

**The Fix:**
```python
# Ensure role is present (unconditional check, outside tool_calls block)
if reconstructed_response["choices"] and "delta" not in reconstructed_response["choices"][0]:
    reconstructed_response["choices"][0]["delta"] = {}
if reconstructed_response["choices"]:
    if "role" not in reconstructed_response["choices"][0].get("delta", {}):
        reconstructed_response["choices"][0]["delta"]["role"] = "assistant"
```

**Lesson:** Always ensure logging consistency. If you inject fields for downstream compatibility, you must also inject them into the logged reconstruction. Don't put conditional checks inside other conditionals that might skip the fix.

---

## Problem: Tool Calls Argument Reconstruction

**Issue:** Tool call arguments arrive as JSON fragments across multiple SSE chunks. Simply concatenating them fails because each chunk contains a full JSON object, not just the argument string.

**Working Pattern:**
```python
for tc_delta in delta["tool_calls"]:
    tc_idx = tc_delta["index"]
    # Merge function fields
    for func_key, func_value in tc_value.items():
        if func_key == "function":
            # Handle nested function fields
            if "function" not in tool_calls_accumulator[idx][tc_idx]:
                tool_calls_accumulator[idx][tc_idx]["function"] = {}
            for inner_key, inner_value in tc_value.items():
                if inner_key in tool_calls_accumulator[idx][tc_idx]["function"]:
                    # Concatenate string arguments
                    tool_calls_accumulator[idx][tc_idx]["function"][inner_key] += inner_value
                else:
                    tool_calls_accumulator[idx][tc_idx]["function"][inner_key] = inner_value
```

**Lesson:** For tool_calls, merge chunk-by-chunk rather than concatenating raw strings. Handle the nested structure properly.

---

## Configuration Best Practices

**Logging Configuration:**
- `MAX_BODY_CHARS=10_000_000` - Capture full response bodies
- `MAX_SSE_BYTES=10_000_000` - Capture full SSE streams  
- `LOG_SNIP_CHARS=0` - Disable truncation
- `LOG_LEVEL="DEBUG"` - Verbose logging for debugging

**OpenAI Compatibility:**
- `ENABLE_OPENAI_STREAM_COMPAT=1` - Inject role and format deltas

**Lesson:** For production debugging, increase buffer sizes and disable truncation. The memory overhead is worth the visibility.

---

## Summary

1. **Use runtime exception type checking** for nested exception handlers
2. **Inject role in first delta** for OpenAI compatibility, handle tool-only chunks specially
3. **Ensure logging consistency** - inject same fields in logged reconstruction as in streamed output
4. **Merge tool_calls chunk-by-chunk** rather than concatenating raw strings
5. **Increase buffer sizes** for full visibility during debugging