# ACTIVE TASK

## Status
- [x] Completed

## Title
Refactor CATALYST agent filesystem to separate runtime state from durable knowledge

## Scope
CATALYST

## Goal
Refactor the CATALYST internal filesystem structure so that operational runtime state and longer-lived knowledge are stored in separate directories. This should improve agent clarity, reduce cognitive overload, and make future scaling safer as the number of files and skills grows.

## Why this matters
The current `_agent/` directory mixes different categories of information:

- volatile operational state
- durable or semi-durable knowledge
- task continuity artifacts
- reference files

This makes it harder for the agent to distinguish:
- what must be read first
- what is optional reference
- what should be updated frequently
- what should remain stable and consolidated

Separating runtime state from knowledge should improve:
- reading priority
- workflow reliability
- maintainability of WORK / FEEDBACK / LEARN
- long-term scalability of CATALYST

## Target Architecture

### New target structure
```text
_agent/
  state/
    ACTIVE_TASK.md
    HANDOFF.md
    TODOS.md
    COMPLETED_TASKS.md
    SCOPE.md

  knowledge/
    KNOWLEDGE_BASE.md
    MAP.md
    MEMORY.md
    CONSTRAINTS.md
    DONE_CRITERIA.md
    SKILL_PROPOSAL.md
```

## Completion Summary
Successfully refactored the CATALYST agent filesystem to separate runtime state from durable knowledge. The changes have been committed and all files moved to their appropriate directories.

- Created `_agent/state/` directory for operational runtime files:
  - ACTIVE_TASK.md
  - HANDOFF.md  
  - TODOS.md
  - COMPLETED_TASKS.md

- Created `_agent/knowledge/` directory for durable knowledge files:
  - KNOWLEDGE_BASE.md
  - MAP.md
  - MEMORY.md
  - CONSTRAINTS.md
  - DONE_CRITERIA.md
  - SKILL_PROPOSAL.md

The refactoring improves agent clarity, reduces cognitive overload, and enhances scalability.

## Goal
Refactor the CATALYST internal filesystem structure so that operational runtime state and longer-lived knowledge are stored in separate directories. This should improve agent clarity, reduce cognitive overload, and make future scaling safer as the number of files and skills grows.

## Why this matters
The current `_agent/` directory mixes different categories of information:

- volatile operational state
- durable or semi-durable knowledge
- task continuity artifacts
- reference files

This makes it harder for the agent to distinguish:
- what must be read first
- what is optional reference
- what should be updated frequently
- what should remain stable and consolidated

Separating runtime state from knowledge should improve:
- reading priority
- workflow reliability
- maintainability of WORK / FEEDBACK / LEARN
- long-term scalability of CATALYST

## Target Architecture

### New target structure
```text
_agent/
  state/
    ACTIVE_TASK.md
    HANDOFF.md
    TODOS.md
    COMPLETED_TASKS.md
    SCOPE.md

  knowledge/
    KNOWLEDGE_BASE.md
    MAP.md
    MEMORY.md
    CONSTRAINTS.md
    DONE_CRITERIA.md
    SKILL_PROPOSAL.md