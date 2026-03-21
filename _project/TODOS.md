# Project TODO Wishlist

## Long-term Enhancements

### Architecture & Design
- [ ] Consider implementing a plugin architecture for different summarization strategies
- [ ] Evaluate potential for distributed processing of large conversation histories
- [ ] Explore support for multiple upstream backends beyond OpenAI-compatible APIs

### User Experience
- [ ] Create CLI interface for easy testing and usage without needing HTTP clients
- [ ] Add web UI for monitoring and configuration management
  - ✅ **Partial**: Terminal dashboard (`perf_dashboard.py`) with interactive controls added
- [ ] Implement user-friendly configuration management with GUI or wizard

### Performance & Scalability
- [ ] Implement horizontal scaling support for handling multiple concurrent requests
- [ ] Add load balancing capabilities between different backend models
- [ ] Support for connection pooling to reduce latency in upstream communications

### Feature Expansion
- [ ] Support for custom prompt templates beyond the current summary strategies
  - ✅ External prompt files supported via `SUMMARY_PROMPT_DIR`
  - Config.yaml support: `custom_summary_prompts` with file paths or direct text
  - **Remaining**: Template variables (`{{TRANSCRIPT}}`, `{{LANG_HINT}}`) not configurable per-route
- [ ] Integration with external knowledge bases or retrieval-augmented generation (RAG)
- [ ] Support for multi-language conversation handling and translation

## Documentation & Learning
- ✅ **Completed**: Performance documentation added (`_docs/PERFORMANCE.md`)
  - Terminal dashboard usage and features documented
  - Benchmarking tool documentation added
  - Key metrics tracked and explained
- [ ] Create comprehensive tutorials for different usage scenarios
- [ ] Add examples of advanced configuration patterns
- [ ] Document best practices for production deployment scenarios

## Testing & Quality
- ✅ **Completed**: Performance benchmarking suite added (`benchmark_routes.py`)
  - Measures response time, TPS (prompt/completion/total), token counts
  - Groups results by backend_model for comparison
  - Supports pattern filtering (e.g., "chat/*", "code/senior")
- [ ] Implement more comprehensive automated testing with real backend integration
- ✅ **Completed**: Stress test scenarios documented in `_docs/PERFORMANCE.md`
- [ ] Create stress test scenarios for edge cases in long conversation handling

## Observability & Debugging
- [ ] Add request tracing/correlation IDs across the pipeline
  - Track summary decisions, cache hits/misses, fallback attempts
  - Export to structured logs or tracing backend (OpenTelemetry)
- [ ] Build debug mode with detailed per-request audit trail
  - Show why summarization was triggered/skipped
  - Display message counts before/after repacking
- [ ] Create summary cache browser tool (`cache_browser.py`)
  - List all cached summaries with fingerprints
  - Show hit/miss statistics per fingerprint
  - Support manual cache invalidation

## Summary Cache Management
- [ ] Implement cache cleanup/eviction policies
  - LRU/LFU eviction based on usage patterns
  - Size-based cleanup (max directory size)
- [ ] Add cache statistics endpoint (`GET /metrics/cache`)
  - Total entries, hit rate, average age, memory footprint
- [ ] Visual cache browser in dashboard
  - Show top-used fingerprints, stale entries, fragmentation

## Advanced Routing Features
- [ ] A/B testing support for route comparison
  - Route 50% of requests to different model variants
  - Collect comparative metrics automatically
- [ ] Circuit breaker pattern for failing backends
  - Track failure rates per backend/model
  - Auto-disable unhealthy backends with configurable thresholds
- [ ] Weighted routing based on latency/load
  - Route to fastest available backend dynamically
  - Health score based on recent response times

## Model Registry & Discovery
- [ ] Build local model registry with metadata
  - Store capabilities, benchmark scores, health status
  - Auto-discover from upstream `/models` endpoint
- [ ] Add `/models/available` endpoint with filtering
  - Filter by capabilities (chat, vision, tools)
  - Filter by health score, latency percentile

## Completed Additions (March 2026)
- ✅ Route-based configuration system with fallback chains
- ✅ Performance monitoring dashboard (`perf_dashboard.py`)
- ✅ Benchmarking tool (`benchmark_routes.py`)
- ✅ Configuration validation tool (`validate_config.py`)
- ✅ Health check module (`keeprollming/healthcheck.py`)
- ✅ Performance documentation (`_docs/PERFORMANCE.md`)
