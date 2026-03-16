# Qwen Bootstrap

This project uses **CATALYST**.

Before doing substantial work, read:
1. [AGENTS.md](AGENTS.md)
2. [_agent/state/SCOPE.md](_agent/state/SCOPE.md)
3. [_agent/state/ACTIVE_TASK.md](_agent/state/ACTIVE_TASK.md)
4. [_agent/state/HANDOFF.md](_agent/state/HANDOFF.md)
5. [_agent/self/MEMORY.md](_agent/self/MEMORY.md)
6. [_project/KNOWLEDGE_BASE.md](_project/KNOWLEDGE_BASE.md) when relevant

Skills visible at runtime are projected into `.qwen/skills/`.
Those skills are markdown procedures, not Python executables.
Do **not** attempt to run `main.py` inside `.qwen/skills/`.
