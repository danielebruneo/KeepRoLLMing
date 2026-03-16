# CATALYST Setup

This document explains how to set up CATALYST on a project from scratch or migrate an older local agent setup.

## Placeholder future remote setup URL
In the future, a runner should be able to do something like:

```text
scrape https://<placeholder-catalyst-host>/SETUP.md and follow instructions to set up CATALYST on this project
```

Replace the placeholder with the real hosted URL once available.

## Setup modes
The setup script supports three common starting points:

1. **Clean project**
   - no existing CATALYST files
   - no existing `.qwen/skills/` projection

2. **Legacy runner bootstrap only**
   - project may have `QWEN.md` and `.qwen/`
   - those assets are archived for later semantic digestion

3. **Legacy / pre-layered CATALYST**
   - project may already contain older `_skills/`, `_agent/`, `_templates/`, `AGENTS.md`, `CATALYST.md`, etc.
   - these are archived to `.catalyst_legacy/` and then processed later by `DIGEST-LEGACY-CATALYST`

## Recommended installation (local clone or unpack)
1. Clone or unpack the CATALYST repository somewhere local.
2. Run:

```bash
/path/to/CATALYST/scripts/setup_catalyst.sh /path/to/target/project
```

If you are already inside the target repository and have copied the CATALYST files there, you can also run:

```bash
./scripts/setup_catalyst.sh .
```

## What the setup script does
The setup script is intentionally **deterministic and conservative**:
- archives legacy CATALYST-like assets into `.catalyst_legacy/migration_<timestamp>/`
- installs the new layered core under `.catalyst/`
- installs `_agent/` and `_project/` skeletons
- installs root bootstrap shims (`AGENTS.md`, `CATALYST.md`, `QWEN.md`, `CLAUDE.md`)
- recreates `.qwen/skills/` as a **clean real directory**
- projects the active markdown skills into `.qwen/skills/`
- writes setup and migration notes for the next agent session

The setup script does **not** try to semantically migrate every legacy file on its own.
That semantic step is handled by the new CATALYST skill:
- `DIGEST-LEGACY-CATALYST`

## After setup
Once the setup script completes:
1. Re-open the agent on the target repository.
2. Ask it to use `DIGEST-LEGACY-CATALYST`.
3. If legacy skills were archived, that skill should also use `RECONCILE-LEGACY-SKILLS`.
4. After skill inventory changes, use `SYNC-QWEN-SKILL-REGISTRY`.

## Qwen runtime note
Qwen expects visible skills under `.qwen/skills/`.
CATALYST handles this by treating `.qwen/skills/` as a **projection registry**.
Do not author skills there directly.

Canonical skill locations are:
1. `_agent/overlay/_skills/`
2. `_project/_skills/`
3. `.catalyst/_skills/`

## Host project human docs
CATALYST does **not** own the host project's `README.md`.
Human-facing project docs should stay in the host repo's root and `_docs/`.
CATALYST only installs bootstrap shims and its own layered structures.

## Future upgrade model
A newer CATALYST release should normally be applied by running its `setup_catalyst.sh` again against the target project.
Legacy assets will be archived, the new core deployed, and semantic digestion can then be performed on top.
