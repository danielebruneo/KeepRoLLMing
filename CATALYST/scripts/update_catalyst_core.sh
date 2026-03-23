#!/usr/bin/env bash
set -euo pipefail

# Update CATALYST core script
# Updates .catalyst/ while preserving root _agent/ and _project/
# Migrates orphaned skills to _project/_skills/
# Then runs sync_qwen_skill_registry.sh

PACKAGE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET_DIR="${1:-.}"
TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"
TS="$(date +%Y%m%d_%H%M%S)"
LEGACY_DIR="$TARGET_DIR/.catalyst_legacy/update_${TS}"
UPDATE_REPORT="$LEGACY_DIR/UPDATE_REPORT.md"

mkdir -p "$LEGACY_DIR"

echo "=== CATALYST Update Script ==="
echo "Target directory: $TARGET_DIR"
echo "Timestamp: $TS"
echo ""

cd "$TARGET_DIR"

# Track what gets migrated
declare -a MIGRATED_SKILLS=()
declare -a PRESERVED_DIRS=()

# Step 1: Update .catalyst/ core (preserve user modifications)
echo "Step 1: Updating .catalyst/ core..."
if [ -d "$TARGET_DIR/.catalyst" ]; then
    # Create a temp directory for the new core
    TEMP_CORE=$(mktemp -d)
    cp -a "$PACKAGE_ROOT/.catalyst" "$TEMP_CORE/"
    
    # Merge: copy new files, preserve existing ones
    while IFS= read -r -d '' file; do
        rel_path="${file#$TEMP_CORE/}"
        dest_file="$TARGET_DIR/$rel_path"
        
        if [ ! -e "$dest_file" ]; then
            # File doesn't exist in target, copy it
            mkdir -p "$(dirname "$dest_file")"
            cp -a "$file" "$dest_file"
            MIGRATED_SKILLS+=("$rel_path")
        fi
        # Else: preserve existing file (user modifications)
    done < <(find "$TEMP_CORE" -type f -print0)
    
    rm -rf "$TEMP_CORE"
    PRESERVED_DIRS+=(".catalyst (merged)")
else
    # No .catalyst/ exists, do a full copy
    cp -a "$PACKAGE_ROOT/.catalyst" "$TARGET_DIR/.catalyst"
    PRESERVED_DIRS+=(".catalyst (new)")
fi

# Step 2: Update _agent/ (preserve user modifications)
echo "Step 2: Updating _agent/ (preserving user modifications)..."
if [ -d "$TARGET_DIR/_agent" ]; then
    TEMP_AGENT=$(mktemp -d)
    cp -a "$PACKAGE_ROOT/_agent" "$TEMP_AGENT/"
    
    while IFS= read -r -d '' file; do
        rel_path="${file#$TEMP_AGENT/}"
        dest_file="$TARGET_DIR/_agent/$rel_path"
        
        if [ ! -e "$dest_file" ]; then
            mkdir -p "$(dirname "$dest_file")"
            cp -a "$file" "$dest_file"
        fi
    done < <(find "$TEMP_AGENT" -type f -print0)
    
    rm -rf "$TEMP_AGENT"
    PRESERVED_DIRS+=("_agent (merged)")
else
    cp -a "$PACKAGE_ROOT/_agent" "$TARGET_DIR/_agent"
    PRESERVED_DIRS+=("_agent (new)")
fi

# Step 3: Update _project/ (preserve user modifications)
echo "Step 3: Updating _project/ (preserving user modifications)..."
if [ -d "$TARGET_DIR/_project" ]; then
    TEMP_PROJECT=$(mktemp -d)
    cp -a "$PACKAGE_ROOT/_project" "$TEMP_PROJECT/"
    
    while IFS= read -r -d '' file; do
        rel_path="${file#$TEMP_PROJECT/}"
        dest_file="$TARGET_DIR/_project/$rel_path"
        
        if [ ! -e "$dest_file" ]; then
            mkdir -p "$(dirname "$dest_file")"
            cp -a "$file" "$dest_file"
        fi
    done < <(find "$TEMP_PROJECT" -type f -print0)
    
    rm -rf "$TEMP_PROJECT"
    PRESERVED_DIRS+=("_project (merged)")
else
    cp -a "$PACKAGE_ROOT/_project" "$TARGET_DIR/_project"
    PRESERVED_DIRS+=("_project (new)")
fi

# Step 4: Handle orphaned skills
echo "Step 4: Checking for orphaned skills..."
CORE_SKILLS_DIR="$PACKAGE_ROOT/.catalyst/_skills"
TARGET_CORE_SKILLS="$TARGET_DIR/.catalyst/_skills"
TARGET_PROJECT_SKILLS="$TARGET_DIR/_project/_skills"

mkdir -p "$TARGET_PROJECT_SKILLS"

# Get list of skills in CATALYST/.catalyst/_skills/
if [ -d "$CORE_SKILLS_DIR" ]; then
    for skill_dir in "$CORE_SKILLS_DIR"/*/; do
        if [ -d "$skill_dir" ]; then
            skill_name=$(basename "$skill_dir")
            core_skill_path="$TARGET_CORE_SKILLS/$skill_name"
            project_skill_path="$TARGET_PROJECT_SKILLS/$skill_name"
            
            # Check if skill exists in root .catalyst/_skills/
            if [ ! -d "$core_skill_path" ]; then
                echo "  Migrating orphaned skill: $skill_name"
                # Migrate to _project/_skills/
                cp -a "$skill_dir" "$TARGET_PROJECT_SKILLS/"
                MIGRATED_SKILLS+=("skills/$skill_name")
            fi
        fi
    done
fi

# Step 5: Recreate _templates symlink
echo "Step 5: Updating _templates symlink..."
rm -rf "$TARGET_DIR/_templates"
ln -s ".catalyst/_templates" "$TARGET_DIR/_templates"

# Step 6: Generate update report
echo "Step 6: Generating update report..."
cat > "$UPDATE_REPORT" <<EOF
# CATALYST Update Report

Timestamp: $TS
Package source: $PACKAGE_ROOT

## Preserved Directories
$(for dir in "${PRESERVED_DIRS[@]}"; do echo "- $dir"; done)

## Migrated Orphaned Skills
$(if [ ${#MIGRATED_SKILLS[@]} -gt 0 ]; then for skill in "${MIGRATED_SKILLS[@]}"; do echo "- $skill"; done; else echo "- None"; fi)

## Next Steps
1. Review changes in the report above
2. Run: \`SYNC-QWEN-SKILL-REGISTRY\` to refresh the runtime skill registry
3. Verify your project still works correctly
EOF

echo ""
echo "=== Update Complete ==="
echo "Preserved: ${PRESERVED_DIRS[*]}"
if [ ${#MIGRATED_SKILLS[@]} -gt 0 ]; then
    echo "Migrated orphaned skills: ${MIGRATED_SKILLS[*]}"
else
    echo "No orphaned skills found"
fi
echo ""
echo "Report saved to: $UPDATE_REPORT"
echo ""
echo "Next: Running sync_qwen_skill_registry.sh..."
echo ""

# Step 7: Run sync script
bash "$PACKAGE_ROOT/scripts/sync_qwen_skill_registry.sh" "$TARGET_DIR"

echo ""
echo "=== All Done ==="