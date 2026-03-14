# Caching Mechanism in KeepRoLLMing

## Overview
KeepRoLLMing implements a rolling summary caching mechanism to avoid redundant summarization of conversations. This system stores previously generated summaries and reuses them when appropriate, improving performance and reducing unnecessary computation.

## Date Reference
This document was last reviewed for accuracy: 14/03/2026

## Key Components

### SummaryCacheEntry Dataclass (summary_cache.py)
Represents cached summary entries with these attributes:
- fingerprint: Unique identifier for conversation context
- start_idx: Starting message index in the original conversation
- end_idx: Ending message index in the original conversation  
- range_hash: Hash of the content in the cached range
- summary_text: The actual generated summary text
- summary_model: Model used to generate this summary
- created_at: Timestamp when entry was saved
- message_count: Number of messages summarized
- token_estimate: Estimated tokens for the summary (used for cache validation)
- source_mode: How/where the summary came from ("cache_append_initial", "cache_append_consolidated")

### Cache Management Functions

1. `conversation_fingerprint()` - Generates unique fingerprint based on user/conversation IDs or first N messages
2. `range_hash()` - Computes hash of a message range for cache validation
3. `resolve_cache_dir()` - Determines where to store/load cache entries (based on user_id/conv_id or generic)
4. `build_cache_filename()` - Creates filename from entry details 
5. `save_cache_entry()`, `load_cache_entries()` - File operations for cache persistence

## Implementation Details

### Cache Append Mode
When using `SUMMARY_MODE="cache_append"`, the system:
1. Calculates fingerprint of conversation context (or uses user/conv IDs)
2. Looks up existing cached entries with matching fingerprint 
3. If found, checks if cached range matches current message content
4. Reuses summary when appropriate, potentially consolidating new messages incrementally

### Key Logic Points in app.py
- `_try_cache_append_repack()` function handles cache lookups for `cache_append` mode
- Uses `find_best_prefix_entry_with_reasons()` to determine best cached prefix 
- When no cache hit:
  - Performs classic head/middle/tail summarization
  - Saves the summarized middle content as a new cache entry (if it's cacheable)
- When cache hit exists, potentially uses incremental consolidation

### Cache Validation and Reuse
- Checks for matching ranges before using cached summaries
- Uses range_hash to verify message content hasn't changed since summary was created  
- Invalidates cache entries when messages change or are outside the valid window

## Configuration Settings

| Setting | Description |
|---------|-------------|
| SUMMARY_CACHE_ENABLED | Enable/disable caching system |
| SUMMARY_CACHE_DIR | Directory path for storing cached summaries |
| SUMMARY_CACHE_FINGERPRINT_MSGS | How many initial messages to use in fingerprint calculation |

This is a core feature that enables efficient reuse of summary operations while maintaining correctness.