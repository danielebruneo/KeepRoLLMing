# Project Memory

This directory contains important notes, learnings, and references that are worth remembering for future work on this project.

## Summary of Key Learnings

### Test Environment Management
- Virtual environment approach is essential for avoiding pytest dependency conflicts
- Using dedicated test scripts ensures clean dependency isolation
- Parallel execution with pytest-xdist significantly improves test performance
- Centralized venv creation logic in `set-tests-venv.sh` script to handle edge cases like empty directories

### Configuration and Setup
- Proper handling of UPSTREAM_BASE_URL without trailing `/v1`
- Environment variables for model configuration (MAIN_MODEL, SUMMARY_MODEL, etc.)
- Clear separation between requirements.txt and requirements-dev.txt

## Specific Memory Files

- [Test Environment Fix](./test_env_fix.md) - Details about virtual environment setup and pytest compatibility issues

## Usage Guidelines

When working on this project, refer to these memory files for:
1. Troubleshooting common issues
2. Best practices for setup and configuration
3. Performance optimization strategies
4. Implementation details that are worth remembering

## Date Keeping Instructions

All memory files should include dates when they are created or updated. This helps track the evolution of project knowledge and ensures that developers can understand when information was relevant.

- When creating new memory files, add a timestamp at the top of the file
- When updating existing memory files, add a note about when the update occurred
- Use consistent date formatting: "YYYY-MM-DD" (e.g., "2026-03-12")

## Recent Changes

### Test Environment Improvements [2026-03-12]
- Created dedicated `set-tests-venv.sh` script to centralize virtual environment creation logic
- Fixed issue where empty `.test_venv` directories would not be properly initialized
- All test scripts (`run-tests.sh`, `run-single-test.sh`, `run-parallel-tests.sh`) now use the centralized venv setup