# CATALYST Core

CATALYST is a layered, markdown-first agent-assisted development framework.

## Layered model
- `.catalyst/` — reusable core workflow, core skills, templates, and core docs
- `_agent/` — runtime state, self-brain, learning reports, and local overlay
- `_project/` — project-specific knowledge, docs, and skills
- `_docs/` — host project human / technical docs
- `.qwen/skills/` — runtime skill projection registry for Qwen

## Runtime rule
If a skill is visible in `.qwen/skills/`, treat it as markdown guidance loaded by the runtime.
Do **not** assume the presence of Python entrypoints such as `main.py`.

## Skill registry model
Canonical skill content lives in:
1. `_agent/overlay/_skills/`
2. `_project/_skills/`
3. `.catalyst/_skills/`

The runtime registry for Qwen is rebuilt into `.qwen/skills/` by `SYNC-QWEN-SKILL-REGISTRY`.

## Setup and migration
CATALYST setup is intentionally split in two phases:

### Phase 1 — deterministic setup
Handled by `scripts/setup_catalyst.sh`.
This phase:
- archives legacy CATALYST-like assets
- installs the new layered core
- creates fresh local skeletons
- recreates `.qwen/skills/` as a clean directory
- projects visible skills into the runtime registry

### Phase 2 — semantic digestion
Handled by the agent via `DIGEST-LEGACY-CATALYST`.
This phase:
- inspects archived legacy assets
- extracts information at the level of **pieces of information**, not just files
- routes information into `_agent/state/`, `_agent/self/`, `_agent/overlay/`, `_project/`, and `_docs/`
- uses `RECONCILE-LEGACY-SKILLS` for legacy skills
- refreshes `.qwen/skills/` with `SYNC-QWEN-SKILL-REGISTRY`

## Core lifecycle
A local instance may:
- learn locally in `_agent/self/`
- override core behavior locally in `_agent/overlay/`
- keep project knowledge in `_project/`
- propose generalizable improvements back to the core through:
  - `PROPOSE-CATALYST-CORE-CHANGE`
  - `PROPOSE-CATALYST-PULL-REQUEST`

## Related docs
- [.catalyst/AGENTS.md](AGENTS.md)
- [_docs/development/WORKFLOW.md](_docs/development/WORKFLOW.md)
- [_docs/architecture/LAYERING.md](_docs/architecture/LAYERING.md)
