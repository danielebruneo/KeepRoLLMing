# CATALYST

**Coding Agent That Actually Listens To Your Shit Thoroughly**

CATALYST is a repository-side boilerplate for coding agents. It gives the agent:
- a stable task file
- a handoff file for session continuity
- a repository map
- memory for non-obvious lessons
- skills/playbooks for recurring work patterns
- separate internal docs vs public docs

## What CATALYST is for
Use CATALYST when you want coding agents to behave more predictably inside a project, without coupling the project to a specific model or runtime.

## Core folders
- [_agent/](_agent/) internal operational state for the agent
- [_docs/](_docs/) project and architecture docs
- [_skills/](_skills/) reusable procedures/playbooks
- [_templates/](_templates/) templates used by skills

## Recommended adoption sequence
1. Review [AGENTS.md](AGENTS.md)
2. Adapt [_agent/COMMANDS.md](_agent/COMMANDS.md)
3. Build [_agent/MAP.md](_agent/MAP.md)
4. Initialize internal docs with [INIT-KNOWLEDGE-BASE](_skills/INIT-KNOWLEDGE-BASE/SKILL.md)
5. Start using [_agent/ACTIVE_TASK.md](_agent/ACTIVE_TASK.md) and [_agent/HANDOFF.md](_agent/HANDOFF.md)

## Notes
CATALYST intentionally does **not** document runtime tool schemas. Those belong to the agent environment, not the repository.
