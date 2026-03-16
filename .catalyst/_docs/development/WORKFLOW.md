# Workflow

## Standard operating loop
1. THINK
2. PLAN (if needed)
3. WORK
4. FEEDBACK
5. LEARN
6. ADAPT (only if local, safe, and reversible)
7. CLOSE-TASK

## Setup / migration loop
1. Run `scripts/setup_catalyst.sh`
2. Re-open the agent on the target project
3. Run `DIGEST-LEGACY-CATALYST`
4. Run `SYNC-QWEN-SKILL-REGISTRY` after skill inventory changes

## Skill registry rule
The `.qwen/skills/` folder is runtime-facing and generated.
Never treat it as the canonical place to author skills.
