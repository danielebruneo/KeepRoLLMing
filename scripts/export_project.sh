#!/bin/bash

# Export script for Keeprollming project
# Creates a tar.gz archive with all essential documentation, code and configuration files
# Excludes cache directories, logs, .qwen directory and test environments

set -e

# Get current timestamp for filename
TIMESTAMP=$(date +"%Y%m%d%H%M%S")
ARCHIVE_NAME="KeepRoLLMing-${TIMESTAMP}.tar.gz"

echo "Creating project archive: ${ARCHIVE_NAME}"

# Create temporary directory structure in a way that avoids nested zip issues
TMP_DIR="/tmp/keeprollming_export_${TIMESTAMP}"
mkdir -p "${TMP_DIR}"

# Copy all files directly to temp dir without subdirectories
cp README.md config.yaml requirements.txt requirements-dev.txt pytest.ini MIGRATION_NOTES.md TEST_FIX_SUMMARY.md QWEN.md AGENTS.md CATALYST.md CLAUDE.md keeprollming.py comprehensive_debug.py final_debug_analysis.py token_count_debug.py token_debug.py "${TMP_DIR}/"

# Copy all directories to temp dir using rsync with --exclude options
rsync -av _agent/ "${TMP_DIR}/_agent/"
rsync -av _docs/ "${TMP_DIR}/_docs/"
rsync -av _skills/ "${TMP_DIR}/_skills/"
rsync -av _templates/ "${TMP_DIR}/_templates/"
rsync -av _prompts/ "${TMP_DIR}/_prompts/"
rsync -av scripts/ "${TMP_DIR}/scripts/"
rsync -av tests/ "${TMP_DIR}/tests/"
rsync -av keeprollming/ "${TMP_DIR}/keeprollming/"

# Create archive directly from the root of temp directory
cd "${TMP_DIR}"
tar -czf "/home/daniele/LLM/orchestrator/${ARCHIVE_NAME}" .

# Clean up
rm -rf "${TMP_DIR}"

echo "Archive created successfully: /home/daniele/LLM/orchestrator/${ARCHIVE_NAME}"