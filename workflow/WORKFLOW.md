# Workflow Guidelines

## Overview
This document outlines the collaborative workflow and task management conventions for this project.

## Task Management Structure

### ACTIVE_TASK.md (CURRENT-TASK.md)
- Used to track the current working task during collaboration
- Contains ongoing work details and progress updates
- Should be kept updated throughout the active work period

### COMPLETED_TASKS.md (TASK-HISTORY.md) 
- Stores completed task history after moving from ACTIVE_TASK.md
- Provides historical context of project development
- Serves as reference for past decisions and implementations

### TODO.md
- Contains a list of possible tasks to pick for future work
- Organized by priority, complexity, or category
- Updated regularly with new opportunities and suggestions

## Collaboration Conventions

1. **Task Tracking**: 
   - Start new tasks in ACTIVE_TASK.md (CURRENT-TASK.md)
   - Move completed tasks to COMPLETED_TASKS.md (TASK-HISTORY.md)  
   - Add new ideas/possibilities to TODO.md

2. **File Organization**:
   - ACTIVE_TASK.md and COMPLETED_TASKS.md are kept in workflow/ directory
   - TODO.md is managed in workflow/ directory for easy access during planning  

3. **Documentation Updates**: 
   - Update documentation files as needed during active work  
   - Move completed task history to COMPLETED_TASKS.md when appropriate

## Version Control Practices

- Regular commits with descriptive messages
- Clear separation of concerns between different types of changes
- Merge completed tasks into main branch appropriately

## Project Structure Overview

This project uses a clean folder structure:
- `workflow/` - for collaboration and task management 
- `project/` - for project-level guidelines and conventions
- `docs/` - for technical documentation  
- Main code remains in `keeprollming/` and `tests/`