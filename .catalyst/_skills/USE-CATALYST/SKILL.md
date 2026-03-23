---
name: USE-CATALYST
description: Enforces CATALYST framework rules, checks workflow compliance, and validates scope adherence.
---

# USE-CATALYST Skill

## Goal
Enforce CATALYST framework rules and validate compliance whenever "catalyst" or "CATALYST" is mentioned in conversation, or when manually invoked.

## Purpose
This meta-skill serves as the primary enforcement layer for the CATALYST framework. It automatically validates:
- Cognitive workflow compliance (THINK → PLAN → WORK → FEEDBACK → LEARN → ADAPT → CLOSE-TASK)
- Scope adherence (files modified vs SCOPE.md)
- Task lifecycle compliance (PICKUP → WORK → CLOSE-TASK)
- Documentation compliance (SKILLS-INDEX.md updates, bootstrap file sync)
- State file integrity (broken links, orphaned tasks)
- **Environmental pattern awareness**: Check ENVIRONMENT.md for relevant LLM/tool patterns

## Auto-Invocation
This skill is automatically invoked when:
- User says "catalyst", "CATALYST", "catalyst rules", "enforce catalyst"
- User asks about CATALYST framework usage
- Before making substantial CATALYST-related changes

## Manual Invocation
```bash
skill: "USE-CATALYST"
# Or with specific checks:
skill: "USE-CATALYST" with check="scope"
skill: "USE-CATALYST" with check="workflow"
skill: "USE-CATALYST" with check="all"
```

## Enforcement Checks

### 1. Cognitive Workflow Compliance
**What it checks:**
- Was THINK used before substantial work?
- Was PLAN used before risky/complex tasks?
- Is WORK following proper sequence?
- Were FEEDBACK and LEARN properly sequenced?
- Did ADAPT follow from LEARN insights?

**Violations detected:**
- Starting WORK without THINK when task is unclear
- Skipping PLAN for complex tasks
- LEARN without prior FEEDBACK analysis
- ADAPT without LEARN insights

**Action:**
- Log violation with timestamp
- Suggest corrective action
- Warn user if pattern is repeated

### 2. Scope Compliance
**What it checks:**
- Files modified match current SCOPE.md boundaries
- Tests-only scope doesn't touch implementation
- Config changes are intentional and scoped

**Violations detected:**
- Modifying files outside allowed patterns
- Tests scope touching implementation files
- Core changes when scope is limited

**Action:**
- Block critical violations
- Warn about non-critical violations
- Suggest UPDATE-SCOPE if legitimate change

### 3. Task Lifecycle Compliance
**What it checks:**
- PICKUP-TASK was used to start task
- ACTIVE_TASK.md was properly cleared (not deleted) on completion
- COMPLETED_TASKS.md has entry for completed tasks
- TODO list links to active/completed tasks

**Violations detected:**
- Work started without PICKUP-TASK
- ACTIVE_TASK.md deleted instead of cleared
- Missing COMPLETED_TASKS entry
- Broken TODO links

**Action:**
- Log lifecycle violations
- Suggest corrective actions
- Recommend WORKFLOW-AUDIT if multiple issues

### 4. Documentation Compliance
**What it checks:**
- SKILLS-INDEX.md updated when skills change
- Bootstrap files (QWEN.md, AGENTS.md, README.md) in sync
- CATALYST core skills unchanged (only project skills modified)

**Violations detected:**
- New skill created but SKILLS-INDEX.md not updated
- Bootstrap files out of sync
- CATALYST core files modified

**Action:**
- Suggest SKILLS-INDEX.md regeneration
- Flag bootstrap file drift
- Block CATALYST core modifications

### 5. Environmental Pattern Check
**What it checks:**
- Relevant patterns in ENVIRONMENT.md for current work context
- LLM model behaviors that might affect approach
- Tool-calling limitations that should be avoided

**Violations detected:**
- Attempting approach with known environmental quirk
- Ignoring documented LLM patterns
- Repeating known tool-calling mistakes

**Action:**
- Warn: "ENVIRONMENT.md shows this pattern has issues"
- Suggest alternative from documented workaround
- Reference specific pattern entry

### 6. State File Integrity
**What it checks:**
- All state files have valid structure
- Links between files are intact
- No orphaned tasks or stale entries
- STALE_TASKS have HANDOFF entries
- TODO_PROPOSALS have approval status

**Violations detected:**
- Empty or corrupted state files
- Broken cross-references
- Orphaned suspended tasks
- Stale proposals (>7 days)

**Action:**
- Recommend WORKFLOW-AUDIT
- Suggest cleanup actions
- Flag critical integrity issues

## When to Use
- **Auto:** User mentions "catalyst" or "CATALYST"
- **Manual:** Before making CATALYST changes
- **Periodic:** Regular compliance checks
- **Before commit:** Validate all changes

## Constraints
- Never block legitimate work without clear justification
- Log all violations for audit trail
- Suggest specific corrective actions
- Distinguish between critical and warning-level issues
- Reference specific CATALYST documentation

## Example Usage

### Auto-Invocation
```
User: "Can you enforce the catalyst rules for this change?"
Agent: [Invokes USE-CATALYST]
  ✅ Scope: All files within boundaries
  ✅ Workflow: THINK used before WORK
  ⚠️  TODO link missing for completed task
  → Suggestion: Update CLOSE-TASK to add TODO reference
```

### Manual Invocation with Specific Check
```bash
skill: "USE-CATALYST" with check="scope"
```
Output:
```
🔍 SCOPE COMPLIANCE CHECK
==========================
Current scope: _agent/state/SCOPE.md
Files modified: 3
  ✅ tests/test_routing.py - Within scope
  ✅ tests/test_config.py - Within scope
  ⚠️  keeprollming/app.py - Outside scope (implementation)
  
Recommendation: UPDATE-SCOPE or skip implementation file
```

### Manual Invocation with Full Audit
```bash
skill: "USE-CATALYST" with check="all"
```
Output:
```
🔍 USE-CATALYST COMPREHENSIVE CHECK
===================================

✅ Cognitive Workflow: Compliant
   - THINK used before substantial work
   - PLAN used for complex task
   - WORK following proper sequence

⚠️  Scope Compliance: 1 warning
   - 1 file outside current scope boundaries

✅ Task Lifecycle: Compliant
   - PICKUP-TASK used to start task
   - ACTIVE_TASK.md properly cleared on completion

⚠️  Documentation: 1 warning
   - SKILLS-INDEX.md not updated (new skill created)

🔍 Recommendations
1. UPDATE-SCOPE for implementation file
2. Regenerate SKILLS-INDEX.md

📊 Full report: _agent/state/WORKFLOW_AUDIT/20260323_170000_audit.md
```

## Related Skills
- **[WORKFLOW-AUDIT]** - Comprehensive state file review
- **[WORKFLOW-CHECK]** - Lifecycle compliance validation
- **[ENFORCE-SCOPE]** - Scope boundary validation
- **[UPDATE-SCOPE]** - Modify scope boundaries
- **[THINK]** - Cognitive workflow routing

## Integration with Workflow
```
1. User mentions "catalyst"
2. USE-CATALYST auto-invoked
   - Checks all compliance areas
   - Generates report
   - Suggests corrections
3. Agent fixes issues
4. Re-run USE-CATALYST to verify
```

## DateTime Tracking
All checks use timestamp format: `DD/MM/YYYY HH:MM:SS`

## Output Location
- Console output: Summary with violations and recommendations
- `_agent/state/WORKFLOW_AUDIT/{timestamp}_audit.md`: Full compliance report