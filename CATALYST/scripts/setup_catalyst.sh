#!/usr/bin/env bash
set -euo pipefail

PACKAGE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET_DIR="${1:-.}"
TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"
TS="$(date +%Y%m%d_%H%M%S)"
LEGACY_DIR="$TARGET_DIR/.catalyst_legacy/migration_${TS}"
SETUP_DIR="$TARGET_DIR/.catalyst_setup"
mkdir -p "$LEGACY_DIR" "$SETUP_DIR"

archive_move_if_exists() {
  local p="$1"
  if [ -e "$p" ] || [ -L "$p" ]; then
    mkdir -p "$LEGACY_DIR"
    mv "$p" "$LEGACY_DIR/"
  fi
}

archive_copy_if_exists() {
  local p="$1"
  local name="$2"
  if [ -e "$p" ] || [ -L "$p" ]; then
    cp -a "$p" "$LEGACY_DIR/$name"
  fi
}

cd "$TARGET_DIR"

# Preserve human/project docs snapshot without deleting project docs from target.
archive_copy_if_exists "_docs" "_docs_snapshot"
archive_copy_if_exists "README.md" "README_snapshot.md"

# Archive legacy CATALYST-like assets.
archive_move_if_exists "AGENTS.md"
archive_move_if_exists "CATALYST.md"
archive_move_if_exists "QWEN.md"
archive_move_if_exists "CLAUDE.md"
archive_move_if_exists ".catalyst"
archive_move_if_exists "_agent"
archive_move_if_exists "_project"
archive_move_if_exists "_skills"
archive_move_if_exists "_templates"
archive_move_if_exists ".qwen"

# Install new layered assets.
cp -a "$PACKAGE_ROOT/.catalyst" "$TARGET_DIR/.catalyst"
cp -a "$PACKAGE_ROOT/_agent" "$TARGET_DIR/_agent"
cp -a "$PACKAGE_ROOT/_project" "$TARGET_DIR/_project"
cp -a "$PACKAGE_ROOT/AGENTS.md" "$TARGET_DIR/AGENTS.md"
cp -a "$PACKAGE_ROOT/CATALYST.md" "$TARGET_DIR/CATALYST.md"
cp -a "$PACKAGE_ROOT/QWEN.md" "$TARGET_DIR/QWEN.md"
cp -a "$PACKAGE_ROOT/CLAUDE.md" "$TARGET_DIR/CLAUDE.md"

# Recreate root _templates shim.
rm -rf "$TARGET_DIR/_templates"
ln -s ".catalyst/_templates" "$TARGET_DIR/_templates"

# Recreate clean Qwen runtime registry.
mkdir -p "$TARGET_DIR/.qwen"
rm -rf "$TARGET_DIR/.qwen/skills"
mkdir -p "$TARGET_DIR/.qwen/skills"

bash "$PACKAGE_ROOT/scripts/sync_qwen_skill_registry.sh" "$TARGET_DIR"

cat > "$TARGET_DIR/.catalyst_legacy/LATEST_MIGRATION.md" <<EOF
# Latest CATALYST Setup / Migration

Timestamp: ${TS}
Legacy archive:
- ${LEGACY_DIR}

Installed layered assets:
- .catalyst/
- _agent/
- _project/
- AGENTS.md
- CATALYST.md
- QWEN.md
- CLAUDE.md

Next steps:
1. Re-open the agent on this repository.
2. Use DIGEST-LEGACY-CATALYST.
3. If legacy skills exist, ensure RECONCILE-LEGACY-SKILLS is used.
4. Run SYNC-QWEN-SKILL-REGISTRY after skill inventory changes.
EOF

cat > "$SETUP_DIR/LATEST_SETUP.md" <<EOF
# Latest CATALYST Setup

Timestamp: ${TS}
Package source:
- ${PACKAGE_ROOT}
Target:
- ${TARGET_DIR}

Mode:
- deterministic install + legacy archive
- semantic migration deferred to DIGEST-LEGACY-CATALYST
EOF

echo "CATALYST setup complete in ${TARGET_DIR}."
echo "Legacy assets archived in ${LEGACY_DIR}."
