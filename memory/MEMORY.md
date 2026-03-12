# Project Memory

This directory contains important notes, learnings, and references that are worth remembering for future work on this project.

## Summary of Key Learnings

### Test Environment Management
- Virtual environment approach is essential for avoiding pytest dependency conflicts
- Using dedicated test scripts ensures clean dependency isolation
- Parallel execution with pytest-xdist significantly improves test performance

### Configuration and Setup
- Proper handling of UPSTREAM_BASE_URL without trailing `/v1` 
- Environment variables for model configuration (MAIN_MODEL, SUMMARY_MODEL, etc.)
- Clear separation between requirements.txt and requirements-dev.txt

## Specific Memory Files

- [Test Environment Fix](./test_env_fix.md) - Details about virtual environment setup and pytest compatibility issues
- [Performance Optimization](./performance_optimization.md) - Notes on test suite performance improvements
- [Caching Mechanism](./caching_mechanism.md) - Key insights about rolling summary caching behavior

## Usage Guidelines

When working on this project, refer to these memory files for:
1. Troubleshooting common issues
2. Best practices for setup and configuration
3. Performance optimization strategies
4. Implementation details that are worth remembering