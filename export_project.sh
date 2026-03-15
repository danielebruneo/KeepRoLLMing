#!/bin/bash

# Export script for Keeprollming project
# Creates a zip archive with all essential documentation, code and configuration files
# Excludes cache directories, logs, .qwen directory and test environments

set -e

# Get current timestamp for filename
TIMESTAMP=$(date +"%Y%m%d%H%M%S")
ARCHIVE_NAME="KeepRoLLMing-${TIMESTAMP}.zip"

echo "Creating project archive: ${ARCHIVE_NAME}"

# Create temporary directory for export
TMP_DIR="/tmp/keeprollming_export_${TIMESTAMP}"
mkdir -p "${TMP_DIR}"

# Copy essential files and directories using find to avoid issues with nested dirs
find . -maxdepth 1 \( -name "*.md" -o -name "config.yaml" -o -name "requirements.txt" -o -name "requirements-dev.txt" -o -name "pytest.ini" -o -name "MIGRATION_NOTES.md" -o -name "TEST_FIX_SUMMARY.md" -o -name "keeprollming.py" \) -exec cp {} "${TMP_DIR}/" \;

# Copy directories, avoiding cache and log files
mkdir -p "${TMP_DIR}/_agent"
cp -r _agent/* "${TMP_DIR}/_agent/"

mkdir -p "${TMP_DIR}/_docs"
find _docs -type f -not -path "*/.pytest_cache/*" -not -path "*/__pycache__/*" -exec cp {} "${TMP_DIR}/_docs/" \;

mkdir -p "${TMP_DIR}/_skills"
cp -r _skills/* "${TMP_DIR}/_skills/"

mkdir -p "${TMP_DIR}/_templates"
cp -r _templates/* "${TMP_DIR}/_templates/"

mkdir -p "${TMP_DIR}/_prompts"
cp -r _prompts/* "${TMP_DIR}/_prompts/"

mkdir -p "${TMP_DIR}/scripts"
cp -r scripts/* "${TMP_DIR}/scripts/"

mkdir -p "${TMP_DIR}/tests"
find tests -type f -not -path "*/.pytest_cache/*" -not -path "*/__pycache__/*" -exec cp {} "${TMP_DIR}/tests/" \;

mkdir -p "${TMP_DIR}/keeprollming"
cp -r keeprollming/* "${TMP_DIR}/keeprollming/"

# Create the zip archive
cd /tmp
zip -r "${ARCHIVE_NAME}" "keeprollming_export_${TIMESTAMP}"

# Move to project root and clean up
mv "${ARCHIVE_NAME}" "/home/daniele/LLM/orchestrator/"
rm -rf "${TMP_DIR}"

echo "Archive created successfully: /home/daniele/LLM/orchestrator/${ARCHIVE_NAME}"