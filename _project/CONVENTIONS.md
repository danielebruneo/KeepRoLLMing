# Project Conventions

## Overview
This document outlines the coding conventions, best practices, and project guidelines for this Keeprollming orchestrator.

## Code Style

### Python Standards
- Follow PEP8 style guide
- Use type hints where appropriate  
- Maintain consistent naming conventions
- Write clear, descriptive docstrings

### FastAPI Guidelines 
- Use proper route definitions and error handling
- Implement async/await patterns correctly
- Structure code in modular components as per existing architecture  

## Documentation Standards 

### Markdown Formatting
- Use consistent heading levels (H1 for main sections)
- Include proper code blocks with language identifiers  
- Maintain clear, readable formatting

### File Naming Convention 
- Use lowercase with underscores for file names (e.g., `rolling_summary.py`)
- Keep descriptive but concise names for files and functions  

## Testing Conventions

### Test Structure
- Unit/integration tests in `tests/test_orchestrator.py`  
- End-to-end tests in `tests/e2e/`
- Regression tests for summary overflow scenarios in `tests/test_summary_overflow_regression.py`

### Test Best Practices
- Mock upstream calls to avoid live LM Studio instance requirement 
- Use pytest with appropriate markers and fixtures
- Maintain test coverage across all major functionality

## Project Structure Conventions

### Directory Organization
- Code modules organized under `keeprollming/` directory
- Tests in dedicated `tests/` directory
- Documentation files in `_docs/` directory
- Task management artifacts in `_tasks/` directory
- Project guidelines in `_project/` directory

### Configuration Management
- Use environment variables for configuration as per existing setup
- Provide sensible defaults where appropriate 
- Document all configurable parameters clearly

## Logging Conventions

### Log Levels
- INFO: General operational information  
- WARN: Warnings about issues that don't stop execution
- ERROR: Errors that cause failures or fallback behavior
- DEBUG: Detailed debugging information (for development only)

### Logging Format
- Consistent timestamp format for logs
- Clear context identifiers in log messages 