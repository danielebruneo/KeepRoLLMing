---
name: SYNC-QWEN-SKILL-REGISTRY
description: Rebuild .qwen/skills as a runtime projection of active markdown skills from overlay, project, and core locations.
---

# SYNC-QWEN-SKILL-REGISTRY

## Goal
Rebuild `.qwen/skills/` as a clean runtime projection of active markdown skills that live elsewhere.

## Important principles
- `.qwen/skills/` is **not** a source-of-truth skill store.
- Skills are **markdown procedures**, not Python executables.
- Do **not** try to run `main.py` from `.qwen/skills/`.

## Canonical skill locations
1. `_agent/overlay/_skills/`
2. `_project/_skills/`
3. `.catalyst/_skills/`

## Resolution order
For each skill name, select the source in this precedence order:
1. `_agent/overlay/_skills/<NAME>/SKILL.md`
2. `_project/_skills/<NAME>/SKILL.md`
3. `.catalyst/_skills/<NAME>/SKILL.md`

## Procedure

This skill contains its own implementation logic to avoid needing external scripts.

The implementation works by:
1. Inspecting available skills in overlay, project, and core locations
2. Resolving the winning source for each skill name according to precedence order (core > project > overlay)
3. Recreating `.qwen/skills/` as a clean real directory, even if it used to be a symlink
4. For each visible skill, materializing:
   - `.qwen/skills/<NAME>/SKILL.md` -> symlink to canonical source 
   - `.qwen/skills/<NAME>/SKILL-<NAME>.md` -> symlink to `SKILL.md`
5. Removing stale runtime skill entries

Here's the actual implementation:

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-.}"
ROOT="$(cd "$ROOT" && pwd)
cd "$ROOT"

# Recreate .qwen/skills as a clean real directory, even if it used to be a symlink.
mkdir -p .qwen
rm -rf .qwen/skills
mkdir -p .qwen/skills
REPORT=".qwen/skills/.sync_report.txt"
: > "$REPORT"

emit_skill() {
  local name="$1"
  local src="$2"
  mkdir -p ".qwen/skills/${name}"
  ln -sfn "$src" ".qwen/skills/${name}/SKILL.md"
  ln -sfn "SKILL.md" ".qwen/skills/${name}/SKILL-${name}.md"
  printf '%s <= %s
' "$name" "$src" >> "$REPORT"
}

declare -A chosen
# Precedence: core first, then project, then overlay overwrites.
for base in ".catalyst/_skills" "_project/_skills" "_agent/overlay/_skills"; do
  [ -d "$base" ] || continue
  for dir in "$base"/*; do
    [ -d "$dir" ] || continue
    name="$(basename "$dir")"
    skill="$dir/SKILL.md"
    [ -f "$skill" ] || [ -L "$skill" ] || continue
    chosen["$name"]="$skill"
  done
done

for name in "${!chosen[@]}"; do
  rel="$(realpath --relative-to=".qwen/skills/$name" "${chosen[$name]}")"
  emit_skill "$name" "$rel"
done

echo "Qwen runtime skill registry rebuilt at $ROOT/.qwen/skills" >> "$REPORT"
echo "Qwen runtime skill registry rebuilt."
```

## When to use
- After adding, removing, or moving any skill
- After editing any skill under core, project, or overlay
- After `DIGEST-LEGACY-CATALYST`
- After setup or upgrades
- After changes noticed in git under skill locations
