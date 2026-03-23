# BUILD-AGENT-MAP Skill

## Goal
Populate [`_agent/knowledge/MAP.md`](../../_agent/knowledge/MAP.md) with an agent navigation guide for the CATALYST framework and project structure.

## Purpose
This skill creates an agent-facing documentation that teaches how to navigate the CATALYST framework, understand the knowledge hierarchy, and find relevant documentation for working with the project.

## Procedure

### 1. Document Entry Points
- **QWEN.md** - Qwen-specific bootstrap entrypoint
- **AGENTS.md** - Canonical workflow specification
- **CATALYST.md** - CATALYST framework specification
- **README.md** - Human-facing project overview

### 2. Explain Knowledge Hierarchy
Document the distinction between:
- **Runtime state** (`_agent/state/`) - Active task, handoff, scope
- **Durable knowledge** (`_agent/knowledge/`) - Project knowledge, skills proposals
- **Project documentation** (`_project/`) - Knowledge base, maps, todos
- **Agent overlay** (`_agent/overlay/`) - Local skill customizations

### 3. Document Cognitive Workflow
Explain the CATALYST cognitive sequence:
1. **THINK** - Clarify objective, select next skill
2. **PLAN** (optional) - Create bounded execution plan
3. **WORK** - Execute the task
4. **FEEDBACK** - Analyze interaction patterns
5. **LEARN** - Consolidate lessons
6. **ADAPT** - Apply small, safe improvements
7. **CLOSE-TASK** - Update handoff, memory, task state

### 4. Document Skills Organization
- **`.catalyst/_skills/`** - 46 core CATALYST skills
- **`_project/_skills/`** - Project-specific skills (IMPROVE-SKILLS)
- **`_agent/overlay/_skills/`** - Local skill customizations
- **`.qwen/skills/`** - Runtime skill registry (auto-generated)

### 5. Document Documentation Structure
- **`_project/KNOWLEDGE_BASE.md`** - Comprehensive project knowledge (main entry)
- **`_project/MAP.md`** - Project repository structure
- **`_docs/`** - Specialized documentation (API, config, performance, testing)
- **`_agent/knowledge/`** - Agent-specific knowledge (this file, MEMORY.md)
- **`_agent/learning_reports/`** - Session-specific learning documentation

### 6. Document Cross-References
Provide links between related documentation:
- Architecture docs
- Decision records
- Configuration guides
- Testing documentation
- Troubleshooting guides

### 7. Generate MAP.md
Create/update `_agent/knowledge/MAP.md` with all navigation information.

## When to Use
- When initializing a new agent session
- After major documentation changes
- When agents need to understand the CATALYST framework
- During project onboarding

## Example Structure for _agent/knowledge/MAP.md

```markdown
# Agent Navigation Guide - CATALYST Framework

## Entry Points
- [QWEN.md] - Qwen-specific bootstrap
- [AGENTS.md] - Canonical workflow
- [CATALYST.md] - CATALYST framework

## Knowledge Hierarchy
### Runtime State (_agent/state/)
- Active task, handoff, scope

### Durable Knowledge (_agent/knowledge/)
- Project knowledge base
- This navigation guide
- Skill proposals

### Project Documentation (_project/)
- Knowledge base (main entry)
- Repository map
- Todos

### Specialized Docs (_docs/)
- Architecture, decisions, testing, etc.

## Cognitive Workflow
THINK → PLAN → WORK → FEEDBACK → LEARN → ADAPT → CLOSE-TASK

## Skills Organization
- Core skills: .catalyst/_skills/
- Project skills: _project/_skills/
- Runtime registry: .qwen/skills/

## Finding Documentation
- Project overview: README.md
- Comprehensive knowledge: _project/KNOWLEDGE_BASE.md
- Project structure: _project/MAP.md
- Agent navigation: _agent/knowledge/MAP.md (this file)
```

## Constraints
- Keep documentation concise and scannable
- Use relative paths for all links
- Maintain consistency with CATALYST framework structure
- Update when skills or documentation structure changes

## Related Skills
- [BUILD-REPO-MAP](../../_skills/BUILD-REPO-MAP/SKILL.md) - Project structure map
- [INDEX-SKILLS](../../_skills/INDEX-SKILLS/SKILL.md) - Skills catalog
- [UPDATE-KNOWLEDGE-BASE](../../_skills/UPDATE-KNOWLEDGE-BASE/SKILL.md) - Knowledge base updates