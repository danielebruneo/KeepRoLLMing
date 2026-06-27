#!/bin/bash

# Export script for CATALYST framework related files only
# Creates a tar.gz archive with all CATALYST-related documentation and configuration files
# Excludes cache directories, logs, .qwen directory and test environments
# Preserves symlinks and file permissions better than zip

set -e

# Get current timestamp for filename
TIMESTAMP=$(date +"%Y%m%d%H%M%S")
ARCHIVE_NAME="CATALYST-${TIMESTAMP}.tar.gz"

echo "Creating CATALYST framework archive: ${ARCHIVE_NAME}"

# Create temporary directory structure 
TMP_DIR="/tmp/catalyst_export_${TIMESTAMP}"
mkdir -p "${TMP_DIR}"

# Copy core CATALYST files
cp CATALYST.md QWEN.md AGENTS.md README.md "${TMP_DIR}/"

# Copy all .md files in root directory (excluding specific directories)
find . -maxdepth 1 -name "*.md" -not -path "./_agent/*" -not -path "./_docs/*" -not -path "./_skills/*" -not -path "./_templates/*" | xargs cp -t "${TMP_DIR}/"

# Copy all _agent/ files (operational state)
cp -r _agent/ "${TMP_DIR}/_agent/"

# Copy documentation folder
cp -r _docs/ "${TMP_DIR}/_docs/"

# Copy templates 
cp -r _templates/ "${TMP_DIR}/_templates/"

# Copy all skill directories related to CATALYST framework (including nested structures)
cp -r _skills/ "${TMP_DIR}/_skills/"

# Create tar.gz archive directly from the root of temp directory
cd "${TMP_DIR}"
tar -czf "/home/daniele/LLM/orchestrator/${ARCHIVE_NAME}" .

# Clean up
rm -rf "${TMP_DIR}"

echo "CATALYST framework archive created successfully: /home/daniele/LLM/orchestrator/${ARCHIVE_NAME}"