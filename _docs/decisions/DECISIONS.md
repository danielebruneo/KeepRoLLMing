# Keeprollming Orchestrator - Architectural Decisions

## Key Design Decisions

### 1. FastAPI Framework Choice
**Decision**: Use FastAPI for the web application framework.
**Rationale**: FastAPI provides excellent async support, automatic API documentation, and type hints that make the code more maintainable and less error-prone.

### 2. Async Processing Model
**Decision**: All upstream communication is handled asynchronously.
**Rationale**: Enables efficient handling of multiple concurrent requests while maintaining low latency for individual requests. This is crucial for proxy applications.

### 3. Rolling Summary Strategy
**Decision**: Implement a rolling summary approach with head/tail preservation.
**Rationale**: This strategy preserves the most recent user messages while summarizing the middle sections, ensuring conversation context remains relevant for model understanding.

### 4. Summary Caching
**Decision**: Implement caching of generated summaries to improve performance.
**Rationale**: Reusing previously generated summaries reduces computational overhead and improves response times for repeated conversations or similar contexts.

### 5. Passthrough Mode
**Decision**: Support direct routing without summarization via `pass/<model_name>` prefix.
**Rationale**: Allows users to bypass the orchestrator's summary logic when needed, useful for specific models or debugging purposes.

### 6. Environment Variable Configuration
**Decision**: Configuration can be set via environment variables with YAML/JSON file fallbacks.
**Rationale**: Provides flexibility for deployment in different environments while maintaining default behavior for local development.

### 7. SSE Streaming Support
**Decision**: Implement proper streaming response handling using Server-Sent Events protocol.
**Rationale**: Maintains compatibility with OpenAI's streaming API and allows efficient real-time response handling.

## Technical Constraints

### 1. Context Window Limitations
**Decision**: Dynamic context window calculation based on upstream model capabilities.
**Rationale**: Models have different context limits, so the orchestrator must query each model for its actual capacity to ensure accurate token management.

### 2. Token Counting Consistency
**Decision**: Use consistent token counting across all processing steps.
**Rationale**: Ensures accurate threshold calculations and prevents overflow issues by maintaining precise token counts throughout the pipeline.

### 3. Error Recovery Strategy
**Decision**: Fallback to passthrough mode when summary generation fails.
**Rationale**: Maintains application availability even when summary logic fails, preventing complete service interruption.

## Cross-referencing
This decisions document is referenced by:
- [_agent/KNOWLEDGE_BASE.md](../../_agent/KNOWLEDGE_BASE.md) - Knowledge base
- [_docs/architecture/OVERVIEW.md](../architecture/OVERVIEW.md) - Architecture overview