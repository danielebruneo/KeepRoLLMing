---
name: DOCUMENTATION-REFERENCE
description: Helps agents understand when and how to reference specific documentation files during their workflows.
---

# DOCUMENTATION-REFERENCE Skill

## Goal
Provide guidance to agents on when and how to reference specific project documentation files based on their current task context.

## Procedure
1. Analyze the current task or request context
2. Identify relevant documentation files that should be referenced
3. Provide specific guidance on how to use each documentation file
4. Explain cross-references between related concepts
5. Suggest appropriate actions based on documentation content

## When to Use
- When starting new tasks that involve documentation review or updates
- When agents need clarification about which documentation files are relevant
- When implementing functionality that requires understanding architectural decisions
- When debugging issues related to configuration, performance, or behavior

## Examples
- When implementing summary caching logic, reference CACHING_MECHANISM.md for understanding the caching system
- When configuring environment variables, check CONFIGURATION.md for complete parameter list and usage examples  
- When debugging performance issues, review PERFORMANCE.md for metrics and optimization strategies
- When making architectural decisions, consult DECISIONS.md to understand previous choices and rationale

## Documentation Reference Guide

### Architecture Documents
- `_docs/architecture/OVERVIEW.md`: High-level understanding of project purpose and boundaries
- `_docs/architecture/INVARIANTS.md`: Core constraints that should remain stable
- `_docs/decisions/DECISIONS.md`: Key architectural decisions and their rationale

### Functional Documents  
- `_docs/CACHING_MECHANISM.md`: Detailed explanation of summary caching system
- `_docs/CONFIGURATION.md`: How configuration works with environment variables and files
- `_docs/RUNNING.md`: Instructions for running the application
- `_docs/TESTING.md`: Testing approach, status, and best practices

### Performance Documents
- `_docs/PERFORMANCE.md`: Performance metrics, optimization strategies, and resource usage analysis

## Cross-Reference Guidance
- Configuration changes should be reflected in RUNNING.md and TESTING.md
- Architecture decisions are documented in DECISIONS.md and referenced in OVERVIEW.md
- Caching mechanism details are used in PERFORMANCE.md for optimization metrics