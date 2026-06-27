#!/usr/bin/env bash
set -euo pipefail

echo "This script removes or archives old markdown structure that is superseded by the new boilerplate."
echo "Review the script before running. Press Ctrl+C to abort."
sleep 3

timestamp="$(date +%Y%m%d_%H%M%S)"
archive_dir="_archive_pre_agentic_cleanup_${timestamp}"
mkdir -p "$archive_dir"

move_if_exists() {
  local path="$1"
  if [ -e "$path" ]; then
    echo "Archiving $path -> $archive_dir/"
    mv "$path" "$archive_dir/"
  fi
}

# Archive legacy markdown control layers
move_if_exists "_tasks"
move_if_exists "_memory"
move_if_exists "_project"

# Archive old standalone markdown files that are likely superseded by the new structure
for f in QWEN.md TEST_FIX_SUMMARY.md test_analysis.md; do
  move_if_exists "$f"
done

# Archive legacy skill directory only if it exists and differs from the new one
if [ -d "_skills" ] && [ ! -f "_skills/CREATE-ACTIVE-TASK/SKILL.md" ]; then
  move_if_exists "_skills"
fi

echo
echo "Cleanup complete."
echo "Archived items are in: $archive_dir"
echo "Next recommended steps:"
echo "  1. Review archived docs"
echo "  2. Merge any still-useful content into _agent/ or _docs/"
echo "  3. Re-run your doc review workflow"
