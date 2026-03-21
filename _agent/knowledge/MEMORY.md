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