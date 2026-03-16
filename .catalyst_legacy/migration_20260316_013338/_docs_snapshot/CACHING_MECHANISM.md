# Rolling Summary Caching Mechanism

## Overview

This orchestrator implements a sophisticated rolling-summary system that prevents context overflow by intelligently caching and reusing summary checkpoints. The mechanism is designed to efficiently manage long conversations without losing important information.

## Key Components

### 1. Cache Structure
- **Fingerprint Generation**: Uses SHA256 hash of the first N messages (configurable via `SUMMARY_CACHE_FINGERPRINT_MSGS`) 
- **Partitioning**: Caches are organized by user ID and conversation ID for proper isolation
- **Entry Format**: Each cache entry includes:
  - Fingerprint identifying context
  - Start/End indices defining the covered range
  - Range hash to verify content integrity
  - Summary text (the compressed representation)
  - Summary model used
  - Token estimate 
  - Source mode tracking

### 2. Cache Storage Location
- Files are stored in a directory structure based on user/conversation identifiers:
  ```
  cache_dir/librechat/user_id/conv_id/
  ```

## How It Works

### Cache Lookup Process
1. **Fingerprint Creation**: Generate unique identifier from first N messages 
2. **Entry Retrieval**: Load all cached entries matching the fingerprint
3. **Best Candidate Selection**: Find best matching entry based on:
   - Start index matches expected start position  
   - End index within bounds of current conversation
   - Range hash integrity check (ensures message content hasn't changed)

### Cache Reuse Strategy

#### Incremental Reuse
When a reusable checkpoint is found, the system prefers incremental reuse over regeneration:
- Uses existing summary + new messages delta to create updated summary  
- This approach reduces computational overhead compared to regenerating entire middle section

#### Skip Save Conditions
Cache entries are only saved under these conditions:
1. Summary text isn't a placeholder (e.g., "[PLACEHOLDER]")
2. Summary is sufficiently long (>8 characters)
3. Summary contains meaningful content beyond boilerplate

### Cache Miss Handling
When no suitable cached entry exists, the orchestrator proceeds with:
- Standard summarization of current middle section 
- New summary gets cached for future reuse 

## Key Features

### 1. User/Conversation Isolation
Caches are separated by user ID and conversation ID to ensure privacy and prevent cross-contamination.

### 2. Integrity Validation  
Each cache entry includes a range hash that validates the content has not changed since caching, preventing stale summaries from being reused.

### 3. Incremental Updates 
When existing summaries can be reused, new messages are incrementally appended rather than regenerating entire summary sections.

### 4. Smart Threshold Management
The system intelligently adjusts token usage to maintain proper context size while utilizing cached summaries when available.

## Configuration

### Environment Variables
- `SUMMARY_CACHE_ENABLED`: Enable/disable caching (default: true)
- `SUMMARY_CACHE_DIR`: Cache storage directory path (default: `./__summary_cache`)
- `SUMMARY_CACHE_FINGERPRINT_MSGS`: Number of messages used for fingerprint generation (default: 1)

### Logging & Debugging
The system logs detailed information about:
- Cache lookup attempts and results  
- Candidate rejection reasons (hash mismatch, bounds error)
- Incremental reuse operations
- Save/skip save decisions

## Benefits

1. **Performance**: Reduces computational overhead by reusing previously generated summaries
2. **Memory Efficiency**: Maintains compact context while preserving essential information 
3. **Consistency**: Ensures consistent summary quality across conversation turns
4. **Scalability**: Allows long conversations to be managed without exceeding context limits