#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-.}"
ROOT="$(cd "$ROOT" && pwd)"
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
