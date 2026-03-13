# Analysis of Failing Summary Test

## The Problem

The test `test_summary_middle_recursively_chunks_on_repeated_overflow` is failing because the mock setup prevents actual message chunking from occurring.

## Root Cause

In the test, we have:
```python
monkeypatch.setattr(rs, '_chunk_messages_for_summary', lambda messages, **kwargs: [messages])
```

This means when `summarize_middle` calls `_chunk_messages_for_summary`, it gets back `[messages]` (the original unsplit message) instead of properly split chunks.

## How the Flow Should Work

1. Input message with 12000 characters
2. `_should_prechunk_summary_call()` determines tokens exceed threshold → `should_prechunk=True`
3. `_chunk_messages_for_summary()` should split oversized messages into smaller chunks that fit within context limits 
4. These chunks are processed recursively until all parts are summarized
5. Multiple summary calls should be made (at least 2)

## Why It Fails

In the recursive retry mechanism, when a context overflow error occurs:
- The system attempts to call `_chunk_messages_for_summary()` again with same oversized message  
- Mock returns `[messages]` - no chunking happens
- `_normalize_retry_chunks()` detects this and returns "forced_split_no_progress"
- This prevents any further processing, so no additional summary calls are made

## Solution Approach

The mock for `_chunk_messages_for_summary` should be replaced with a proper implementation that actually splits messages when needed, or we need to make the test more accurate about how it expects chunking to work.

Looking at the real logic in `_chunk_messages_for_summary`, it:
1. Uses `_split_oversized_message()` to split oversized content
2. Then groups messages into chunks based on token limits 
3. The actual splitting is handled by `_split_text_preserve_lines` and related functions

For a proper test, we should either:
- Remove the mock or make it properly emulate chunking behavior  
- Or adjust the expectations in the test to match what actually happens with the current mocks