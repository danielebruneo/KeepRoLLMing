# Agent Navigation Guide - CATALYST Framework

## Entry Points

### Primary Entry Points
- **[QWEN.md](../QWEN.md)** - Qwen-specific bootstrap entrypoint
- **[AGENTS.md](../AGENTS.md)** - Canonical workflow specification for coding agents
- **[CATALYST.md](../CATALYST.md)** - CATALYST framework specification

### Secondary Entry Points
- **[README.md](../README.md)** - Human-facing project overview with brief agent section
- **[_project/KNOWLEDGE_BASE.md](../_project/KNOWLEDGE_BASE.md)** - Comprehensive project knowledge (main knowledge entry)
- **[_project/MAP.md](../_project/MAP.md)** - Project repository structure map

## Knowledge Hierarchy

### Runtime State (`_agent/state/`)
Files that change frequently during agent sessions:
- **`SCOPE.md`** - Current session scope and boundaries
- **`ACTIVE_TASK.md`** - Currently active task details
- **`HANDOFF.md`** - Handoff information between sessions
- **`STATE_SNAPSHOT.md`** - Current state snapshot

**Usage:** Read first to understand current context before making changes.

### Durable Knowledge (`_agent/knowledge/`)
Persistent knowledge that agents should reference:
- **`MAP.md`** - This navigation guide (you are here)
- **`MEMORY.md`** - Implementation patterns, lessons learned, configuration
- **`ENVIRONMENT.md`** - Environmental quirks, LLM patterns, and tool-calling workarounds
- **`SKILL_PROPOSAL.md`** - Skill improvement proposals

**Usage:** Reference as needed for project-specific patterns and conventions.

### Project Documentation (`_project/`)
Project-level documentation and metadata:
- **`KNOWLEDGE_BASE.md`** - Comprehensive project knowledge (main entry point)
- **`MAP.md`** - Repository structure and file organization
- **`TODOS.md`** - Project enhancement wishlist
- **`COMMANDS.md`** - Available commands and scripts
- **`CONSTRAINTS.md`** - Project constraints and guidelines
- **`DECISIONS.md`** - Architecture decision records
- **`_skills/`** - Project-specific skills
  - `IMPROVE-SKILLS/` - Skill enhancement procedures
  - `SKILLS-INDEX.md` - Complete skills catalog

**Usage:** Primary source for project-specific knowledge and structure.

### Agent Overlay (`_agent/overlay/`)
Local customizations that extend core CATALYST:
- **`_skills/`** - Local skill customizations
- **`README.md`** - Overlay documentation

**Usage:** Reference when core skills need project-specific adjustments.

### Specialized Documentation (`_docs/`)
Specialized documentation for specific concerns:
- **`architecture/`** - System architecture and design
- **`decisions/`** - Architecture decision records
- **`design/`** - Design documentation
- **`development/`** - Development guidelines
- **`API_DOCUMENTATION.md`** - API reference
- **`CONFIGURATION.md`** - Configuration guide
- **`PERFORMANCE.md`** - Performance optimization
- **`TESTING.md`** - Testing guidelines
- **`TROUBLESHOOTING.md`** - Common issues and solutions
- **`RUNNING.md`** - Running the application
- **`CACHING_MECHANISM.md`** - Caching details

**Usage:** Deep-dive into specific topics when needed.

### Learning Reports (`_agent/learning_reports/`)
Session-specific learning documentation:
- **Session reports** - Detailed learning from agent sessions
- **Analysis documents** - Deep dives into specific issues

**Usage:** Reference historical learning and avoid repeating mistakes.

## Cognitive Workflow

### Standard Sequence
1. **THINK** - Clarify objective, check scope, select next skill
2. **PLAN** (optional) - Create bounded execution plan for complex tasks
3. **WORK** - Execute the task
4. **FEEDBACK** - Analyze interaction patterns and friction
5. **LEARN** - Consolidate lessons and improvements
6. **ADAPT** - Apply small, safe local improvements
7. **CLOSE-TASK** - Update handoff, memory, and task state

### Cognitive Routing
- **THINK** is the cognitive router - prefer this when next step is unclear
- **FEEDBACK** analyzes recent friction and recommends outcomes
- **LEARN** handles broader consolidation, may recommend THINK or ADAPT
- **ADAPT** applies minimal workflow/skill refinements (small, local, low-risk only)

### Skill Categories
- **Task Management** - PICKUP-TASK, CLOSE-TASK, UPDATE-TODO, CREATE-ACTIVE-TASK
- **Documentation** - IMPROVE-DOC, CONSOLIDATE-DOC, REVIEW-DOC, UPDATE-README
- **Workflow & Planning** - WORK, PLAN, THINK, FEEDBACK, LEARN, ADAPT
- **Knowledge Management** - UPDATE-KNOWLEDGE-BASE, REMEMBER, DISTILL-LEARNINGS
- **Repository Maintenance** - BUILD-REPO-MAP, BUILD-AGENT-MAP, SYNC-COMMANDS
- **Code Quality** - SAFE-REFACTOR, FIX-FAILING-TEST, IMPLEMENT-FEEDBACK
- **Catalyst Core** - DIGEST-LEGACY-CATALYST, RECONCILE-LEGACY-SKILLS, PROPOSE-CORE-CHANGE

## Skills Organization

### Core CATALYST Skills (`.catalyst/_skills/`)
46 core skills for general development workflow.
- **View catalog:** `_project/_skills/SKILLS-INDEX.md`
- **Runtime registry:** `.qwen/skills/` (auto-generated symlinks)

### Project-Specific Skills (`_project/_skills/`)
Skills tailored for this project:
- **IMPROVE-SKILLS** - Enhance existing skills
- **SKILLS-INDEX.md** - Skills catalog

### Runtime Skills (`.qwen/skills/`)
Auto-generated symlinks to active skills for Qwen Code.
- **Regenerate:** Run `SYNC-QWEN-SKILL-REGISTRY` skill

## Finding Documentation

### Quick Reference
| Need | File |
|------|------|
| How to use CATALYST | `_agent/knowledge/MAP.md` (this file) |
| Project structure | `_project/MAP.md` |
| Comprehensive knowledge | `_project/KNOWLEDGE_BASE.md` |
| Configuration details | `_docs/CONFIGURATION.md` |
| Testing guide | `_docs/TESTING.md` |
| Performance info | `_docs/PERFORMANCE.md` |
| Troubleshooting | `_docs/TROUBLESHOOTING.md` |
| API reference | `_docs/API_DOCUMENTATION.md` |

### Documentation Flow
```
QWEN.md
  → AGENTS.md
    → _agent/knowledge/MAP.md (agent navigation)
      → _project/KNOWLEDGE_BASE.md (comprehensive knowledge)
        → _docs/* (specialized docs)
```

## Related Skills

- **[BUILD-AGENT-MAP](../_skills/BUILD-AGENT-MAP/SKILL.md)** - Update this navigation guide
- **[BUILD-REPO-MAP](../_skills/BUILD-REPO-MAP/SKILL.md)** - Update project structure map
- **[INDEX-SKILLS](../_skills/INDEX-SKILLS/SKILL.md)** - Update skills catalog
- **[UPDATE-KNOWLEDGE-BASE](../_skills/UPDATE-KNOWLEDGE-BASE/SKILL.md)** - Update comprehensive knowledge
- **[SYNC-QWEN-SKILL-REGISTRY](../_skills/SYNC-QWEN-SKILL-REGISTRY/SKILL.md)** - Update runtime registry

---

*This navigation guide is maintained by the BUILD-AGENT-MAP skill. Update it when the CATALYST framework structure changes.*