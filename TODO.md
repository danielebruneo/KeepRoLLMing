# TODO: Test Cases for KeepRoLLMing Refactoring

## Summary Logic Tests

1. **Test context overflow handling with retry logic**
   - Scenario: Send a request that exceeds the context limit, causing a context overflow error.
   - Expected behavior: The orchestrator should retry with reduced context and handle the error gracefully.

2. **Test incremental summary reuse from cache**
   - Scenario: Use cache_append mode with existing cached summary to test incremental reuse logic.
   - Expected behavior: When a reusable checkpoint is found, the system should prefer incremental reuse over regenerating middle content.

3. **Test streaming response reconstruction**
   - Scenario: Send a streaming request and verify that the reconstructed response matches expected format.
   - Expected behavior: The proxy correctly reconstructs SSE chunks into full assistant messages.

4. **Test summary caching with different prompt types**
   - Scenario: Test various prompt types (classic, structured, curated) to ensure cache entries are properly generated for each type.
   - Expected behavior: Cache entries should be created and retrieved based on the correct prompt template used.

5. **Test passthrough mode bypassing summarization**
   - Scenario: Use pass/<model_name> to test that no summarization occurs in passthrough mode.
   - Expected behavior: The request is forwarded directly without any summary processing, preserving original messages.
