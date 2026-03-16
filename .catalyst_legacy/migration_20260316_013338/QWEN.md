# Qwen Agent Bootstrap

<!-- CATALYST-BOOTSTRAP:START -->
This repository uses the **[CATALYST](CATALYST.md)** workflow for agent-assisted development.

Before starting substantial work, read these files in order:
1. [AGENTS.md](AGENTS.md)
2. [_agent/ACTIVE_TASK.md](_agent/ACTIVE_TASK.md)
3. [_agent/HANDOFF.md](_agent/HANDOFF.md)
4. [_agent/CONSTRAINTS.md](_agent/CONSTRAINTS.md)
5. [_agent/DONE_CRITERIA.md](_agent/DONE_CRITERIA.md)
6. [_agent/KNOWLEDGE_BASE.md](_agent/KNOWLEDGE_BASE.md)
7. [_agent/MAP.md](_agent/MAP.md)
8. [_agent/COMMANDS.md](_agent/COMMANDS.md)

Use [README.md](README.md) for the human/public project overview.

The block between `CATALYST-BOOTSTRAP:START` and `CATALYST-BOOTSTRAP:END` is canonical bootstrap logic and must be preserved when updating this file.
<!-- CATALYST-BOOTSTRAP:END -->

## Project Snapshot

KeepRoLLMing is a FastAPI proxy/orchestrator in front of an OpenAI-compatible backend (for example LM Studio).
Its main value is rolling-summary support to avoid context overflow while preserving recent conversational state.

### Core Areas
- HTTP entrypoint and orchestration: `keeprollming/app.py`
- Configuration and profile resolution: `keeprollming/config.py`
- Rolling summary logic: `keeprollming/rolling_summary.py`
- Summary cache: `keeprollming/summary_cache.py`
- Upstream transport: `keeprollming/upstream.py`

### Agent Notes
- In CATALYST skill directories, `SKILL.md` is the canonical file. Any `SKILL-<NAME>.md` companion should be treated as an alias/symlink path to the canonical content. Prefer editing `SKILL.md`.
- Do not redefine runtime tool schemas in repository docs. Follow the runtime's actual tool contract.
