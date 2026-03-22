# Memory and Lessons Learned

Use this file for non-obvious lessons that are likely to matter again.

## Entry format
- Date/session: DD/MM/YYYY HH:MM:SS
- Topic: [short description]
- Lesson: [detailed explanation of the lesson learned]
- Relevant files: [path/to/file](path/to/file)
- Category: [learning type] (optional)

---

## Terminal Handling for Interactive Dashboards

**Date/session:** 21/03/2026 14:35:00  
**Topic:** Raw terminal mode and Ctrl+C signal handling  
**Lesson:** When implementing interactive terminal UIs with key bindings, raw terminal mode must be applied **temporarily within the main loop** (not in a background thread) to preserve `Ctrl+C` signal handling.

**Why Background Thread Failed:** Setting terminal mode in a background thread conflicts with the main loop's keyboard interrupt handling, breaking `Ctrl+C`.

**Working Pattern:**
1. Save original terminal settings before entering loop
2. Apply non-canonical mode (`ICANON`, `ECHO` disabled) temporarily  
3. Use `os.read(fd, 1)` with `select.select()` for non-blocking key capture
4. Always restore original settings in `finally` block

This ensures:
- Key presses captured immediately without waiting for Enter
- `Ctrl+C` still works (terminal signals aren't disabled)
- Terminal returns to normal state on exit

**Relevant files:** [perf_dashboard.py](perf_dashboard.py), [_agent/knowledge/MEMORY.md](_agent/knowledge/MEMORY.md)  
**Category:** Implementation Pattern

---

## Configuration-Based Prompt Templates

**Date/session:** 21/03/2026 14:35:00  
**Topic:** Distinguishing file paths from direct text in config.yaml  
**Lesson:** When implementing custom summary prompts from config.yaml, the main challenge was properly distinguishing between file references (strings starting with `./`, `/` or containing path separators) and direct text content.

File-based references should be identified by strings that start with "./", "/", or contain path separators to be treated as paths. Direct text content that doesn't match these patterns should be used literally as prompt templates in config files.

**Relevant files:** [keeprollming/rolling_summary.py](keeprollming/rolling_summary.py), [_docs/CONFIGURATION.md](_docs/CONFIGURATION.md)  
**Category:** Configuration Pattern

---

## Server State Awareness After Code Changes

**Date/session:** 22/03/2026 01:35:00  
**Topic:** Verifying running processes before testing code changes  
**Lesson:** When modifying Python files that affect runtime behavior (app.py, config.py), the server process must be restarted for changes to take effect. Simply saving files does not trigger reload in uvicorn without `--reload` flag.

**Verification Pattern:**
1. Before testing changes: `ps aux | grep uvicorn` to check if process is running
2. After modifying code: Restart server with `pkill -f uvicorn && sleep 1 && uvicorn ... &`
3. Wait for startup logs before sending test requests

**Common Pitfall:** Assuming "unknown" in dashboard means code bug, when actually the issue is stale process running old code.

**Relevant files:** [keeprollming/app.py](keeprollming/app.py), [keeprollming/config.py](keeprollming/config.py), [perf_dashboard.py](perf_dashboard.py)  
**Category:** Runtime State Management

---

## Virtual Environment Context for Project Scripts

**Date/session:** 22/03/2026 01:35:00  
**Topic:** Using venv Python vs system Python for project tools  
**Lesson:** Project scripts (dashboard, benchmark tools) require dependencies installed in the virtual environment. Running with system `python3` causes ModuleNotFoundError.

**Correct Usage:**
```bash
# Option 1: Direct venv path (recommended)
/home/daniele/LLM/orchestrator/.venv/bin/python3 perf_dashboard.py

# Option 2: Activate venv first
cd /home/daniele/LLM/orchestrator && source .venv/bin/activate && python3 perf_dashboard.py
```

**Why This Happens:** Project dependencies (pyyaml, rich, etc.) are installed in `.venv`, not system Python.

**Relevant files:** [perf_dashboard.py](perf_dashboard.py), [benchmark_routes.py](benchmark_routes.py)  
**Category:** Environment Management

---

## Route Name vs Upstream Model Name Separation

**Date/session:** 22/03/2026 01:35:00  
**Topic:** Handling hash IDs from upstream services  
**Lesson:** When upstream services (like Lemonade) return hash IDs instead of readable model names, display the route name separately in UI. This clarifies that "base/alt" is the orchestrator's route while "ea4dc5c6..." is what upstream returned.

**Implementation Pattern:**
1. Track `route_name` separately from `model` field
2. In performance logs: store both fields
3. In dashboard: show Route column (human-readable) and Model column (upstream response)

**Why This Matters:** Users need to know which route they hit, not just what hash ID upstream returned.

**Relevant files:** [keeprollming/performance.py](keeprollming/performance.py), [keeprollming/app.py](keeprollming/app.py), [perf_dashboard.py](perf_dashboard.py)  
**Category:** Data Model Design

---

## Documentation Consolidation Best Practices

**Date/session:** 21/03/2026 14:35:00  
**Topic:** Moving prompt template docs to canonical location  
**Lesson:** When consolidating documentation, identify the most appropriate canonical file for each topic and remove duplication from other files. For configuration details, `_docs/CONFIGURATION.md` is the canonical source; for project overview, use `_project/KNOWLEDGE_BASE.md`; for repository structure, use `_project/MAP.md`.

**Relevant files:** [_docs/CONFIGURATION.md](_docs/CONFIGURATION.md), [_project/KNOWLEDGE_BASE.md](_project/KNOWLEDGE_BASE.md), [_project/MAP.md](_project/MAP.md)  
**Category:** Documentation Pattern

---

## Skill Structure Consistency

**Date/session:** 21/03/2026 14:35:00  
**Topic:** CATALYST skill file organization  
**Lesson:** All CATALYST skills must follow a consistent directory structure with:
1. A main documentation file named `SKILL-NAME.md`
2. A symlink named `SKILL.md` pointing to this main file
3. This pattern ensures proper recognition by the agent system during sync operations

When creating or modifying skills, always ensure both files exist and are properly linked with correct relative paths.

**Relevant files:** [.qwen/skills/](.qwen/skills/)  
**Category:** System Pattern

---

## Route-Based Configuration Implementation

**Date/session:** 21/03/2026 14:35:00  
**Topic:** Fallback chain routing with loop prevention  
**Lesson:** When implementing fallback chains for automatic rerouting, track visited models per request to prevent infinite loops. Use a set data structure to maintain the list of already-attempted models and check before each fallback attempt.

**Relevant files:** [keeprollming/routing.py](keeprollming/routing.py), [keeprollming/app.py](keeprollming/app.py)  
**Category:** Implementation Pattern

---

## Total TPS Metric Calculation

**Date/session:** 21/03/2026 15:45:00  
**Topic:** End-to-end throughput calculation for performance monitoring  
**Lesson:** The `total_tps` metric represents overall system throughput, calculated as `(prompt_tokens + completion_tokens) / elapsed_time`. This differs from `completion_tps` which only measures generation speed.

**Formula:**
```python
total_tps = (prompt_tokens + completion_tokens) / (elapsed_ms / 1000.0)
```

This metric is crucial for understanding real-world performance because it accounts for both prompt processing time and generation time, giving a complete picture of tokens processed per second from request start to finish.

**Implementation:**
- Calculated in `keeprollming/performance.py::compute_request_performance()`
- Aggregated with avg/min/max stats in `_update_summary()` function
- Displayed as "Tot TPS" column in `perf_dashboard.py`
- Stored in `summary.yaml` alongside other TPS metrics (completion_tps, prompt_tps)

**Relevant files:** [keeprollming/performance.py](keeprollming/performance.py), [perf_dashboard.py](perf_dashboard.py), [_docs/PERFORMANCE.md](_docs/PERFORMANCE.md)  
**Category:** Performance Monitoring