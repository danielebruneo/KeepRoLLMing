# CATALYST

CATALYST is a layered, markdown-first agent-assisted development framework.

This repository contains:
- the reusable **core** under `.catalyst/`
- a minimal local **agent instance skeleton** under `_agent/`
- a minimal local **project brain skeleton** under `_project/`
- bootstrap files for runners such as Qwen Code (`QWEN.md`) and Claude (`CLAUDE.md`)
- scripts to **set up**, **sync**, and later **upgrade** a project to the layered CATALYST structure

Read [SETUP.md](SETUP.md) to install CATALYST into a project.

## Key principles
- Skills are **markdown procedures**, not Python executables.
- `.qwen/skills/` is a **runtime projection registry**, not the canonical skill store.
- Canonical skill content lives in one of:
  1. `_agent/overlay/_skills/`
  2. `_project/_skills/`
  3. `.catalyst/_skills/`
- `SKILL.md` is canonical; `SKILL-<NAME>.md` is an alias/symlink to the canonical file.

## Core directories
- `.catalyst/` — CATALYST core
- `_agent/` — local runtime state, self-brain, and overlay
- `_project/` — project-specific knowledge, docs, and skills
- `_docs/` — root human/project docs in the host repository
- `.qwen/skills/` — generated skill registry for Qwen runtime

## Setup flow
1. Clone or unpack this repository somewhere local.
2. Read [SETUP.md](SETUP.md).
3. Run `scripts/setup_catalyst.sh /path/to/target/project`.
4. Re-open the agent on the target project.
5. Run `DIGEST-LEGACY-CATALYST` if legacy assets were archived.

## Placeholder future hosted setup URL
When published, this file is expected to be available from a URL like:
- `https://<placeholder-catalyst-host>/SETUP.md`
