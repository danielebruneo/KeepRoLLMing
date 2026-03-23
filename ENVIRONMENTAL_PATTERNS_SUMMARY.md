# Environmental Pattern Learning System

**Date:** 2026-03-23

## Overview

This system ensures agents learn from their mistakes by automatically capturing environmental quirks, LLM model behaviors, and tool-calling patterns when initial approaches fail. These patterns are stored in `_agent/knowledge/ENVIRONMENT.md` and referenced to prevent repeated mistakes across sessions.

---

## Problem Statement

**Issue:** Agents often struggle with environment-specific or LLM-specific quirks, then repeatedly fall into the same traps when encountering similar situations in future sessions.

**Root Cause:** No systematic way to capture and store workarounds discovered through trial and error.

**Solution:** Environmental Pattern Learning System with automatic capture on first failure.

---

## Core Components

### 1. `_agent/knowledge/ENVIRONMENT.md`

**Purpose:** Dedicated repository for environmental/LLM/tool-calling patterns.

**Structure:**
```markdown
## [Category Name]
### [Specific Issue]
- **Trigger:** When [condition]
- **Failed approach:** [what didn't work]
- **Error:** [exact error]
- **Workaround:** [working solution]
- **Code:** [working code snippet]
- **Why it works:** [explanation]
- **Related:** [file/skill references]
- **Discovered:** [date]
```

**Categories:**
- **LLM Model Patterns:** Model-specific behaviors (OpenAI, Anthropic, etc.)
- **Tool Calling Patterns:** API/tool limitations and workarounds
- **Environment Quirks:** Platform, OS, runtime, or language-specific issues

### 2. `CAPTURE-ENVIRONMENT-LESSON` Skill

**Purpose:** Automatically save patterns when first approach fails.

**Auto-trigger conditions:**
1. First approach fails with unexpected error
2. Tool call fails with error not in documentation
3. Environment-specific limitation discovered
4. Workaround implemented successfully

**Procedure:**
1. Capture failure context (original approach, error, expected vs actual)
2. Document workaround (successful approach, code example, why it works)
3. Categorize pattern (LLM Model, Tool Calling, or Environment)
4. Save to ENVIRONMENT.md with structured format
5. Add to MEMORY.md "Known Issues & Solutions" section
6. Update HANDOFF.md with note

**Example Usage:**
```python
# First approach fails
except httpx.ConnectError:  # NameError!

# Find workaround
except Exception as e:
    exc_type = sys.exc_info()[1].__class__
    if exc_type == httpx.ConnectError:
        handle_error()

# CAPTURE-ENVIRONMENT-LESSON saves this pattern
```

---

## Integration Points

### Updated Skills

#### FEEDBACK
- Added "Environmental Pattern Detection" to analysis framework
- Identifies tool-calling, LLM model, or environment quirks worth capturing
- Suggests CAPTURE-ENVIRONMENT-LESSON when patterns found

#### REMEMBER
- Updated to include ENVIRONMENT.md as valid memory location
- Added instruction: "Save environmental learnings to both ENVIRONMENT.md and MEMORY.md"
- Example: "Remember environment quirks: HTTPX exception handling in nested scopes"

#### USE-CATALYST
- Added "Environmental Pattern Check" (Check #5)
- Before attempting tool calls, checks ENVIRONMENT.md for relevant patterns
- Warns: "ENVIRONMENT.md shows this pattern has issues"
- Suggests alternative from documented workaround

#### MAP
- Added ENVIRONMENT.md to Durable Knowledge section
- Description: "Environmental quirks, LLM patterns, and tool-calling workarounds"

### Updated Entry Points

#### QWEN.md
Added "Pattern Learning" section:
- Link to CAPTURE-ENVIRONMENT-LESSON skill
- Link to ENVIRONMENT.md file
- Rule: "When first approach fails and you find a workaround, automatically save the pattern to ENVIRONMENT.md"

#### AGENTS.md
Added "Pattern Learning" section:
- Description of ENVIRONMENT.md
- Rule for automatic pattern capture
- Rationale: "Prevents repeating the same mistakes in future sessions"

#### SKILLS-INDEX.md
- Updated Task Management & Workflow count: 16 → 17 skills
- Added CAPTURE-ENVIRONMENT-LESSON with description

---

## Example Workflow

### Scenario: HTTPX Exception in Nested Async Generator

**1. First approach fails:**
```python
async def _iter():
    try:
        response = await client.get(url)
    except httpx.ConnectError:  # NameError!
        handle_error()
```

**2. Error received:**
```
NameError: name 'httpx' is not defined
```

**3. Agent finds workaround:**
```python
async def _iter():
    try:
        response = await client.get(url)
    except Exception as e:
        exc_type = sys.exc_info()[1].__class__
        if exc_type == httpx.ConnectError:
            handle_error()
```

**4. CAPTURE-ENVIRONMENT-LESSON auto-triggered:**
- Creates entry in `_agent/knowledge/ENVIRONMENT.md`
- Adds to `_agent/knowledge/MEMORY.md` "Known Issues & Solutions"
- Formats: Problem → Root Cause → Solution → Lesson

**5. Future sessions:**
- USE-CATALYST checks ENVIRONMENT.md before attempting exception handling
- Agent knows this pattern upfront
- No repeated mistakes!

---

## Benefits

### 1. Prevents Repeated Mistakes
- Once a pattern is captured, it's available for all future sessions
- USE-CATALYST proactively warns about known problematic approaches

### 2. Reduces Debugging Time
- No need to rediscover workarounds
- Pattern repository grows with each session

### 3. Institutional Knowledge
- Patterns persist across agent sessions
- New agents benefit from previous learnings

### 4. Continuous Improvement
- Each failure becomes a learning opportunity
- System gets smarter over time

---

## How to Use

### For Agents

**When discovering a workaround:**
1. Immediately call `CAPTURE-ENVIRONMENT-LESSON`
2. Include all details: failed approach, error, workaround, why it works
3. Link to related files and skills
4. Date-stamp the entry

**Before attempting tool calls:**
1. USE-CATALYST will check ENVIRONMENT.md automatically
2. Review relevant patterns for current context
3. Use documented workarounds

### For Users

**When you notice agent struggling:**
- Suggest: "Try using CAPTURE-ENVIRONMENT-LESSON to save this pattern"
- Or: "Check ENVIRONMENT.md for relevant patterns"

**To review patterns:**
- Read `_agent/knowledge/ENVIRONMENT.md`
- See all captured environmental quirks and workarounds

---

## Files Created/Updated

**New:**
- `_agent/knowledge/ENVIRONMENT.md` - Pattern repository
- `.catalyst/_skills/CAPTURE-ENVIRONMENT-LESSON/SKILL.md` - Auto-save skill

**Updated:**
- `_agent/knowledge/MAP.md` - Added ENVIRONMENT.md to navigation
- `.catalyst/_skills/FEEDBACK/SKILL.md` - Added pattern detection
- `.catalyst/_skills/REMEMBER/SKILL.md` - Added ENVIRONMENT.md reference
- `.catalyst/_skills/USE-CATALYST/SKILL.md` - Added pattern check
- `QWEN.md` - Added pattern learning section
- `AGENTS.md` - Added pattern learning section
- `_project/_skills/SKILLS-INDEX.md` - Added CAPTURE-ENVIRONMENT-LESSON

---

## Best Practices

### 1. Capture Immediately
- Don't wait until session end
- Save pattern as soon as workaround found

### 2. Be Specific
- Include exact error messages
- Provide working code examples
- Explain why the workaround works

### 3. Link Context
- Reference related files and skills
- Note the specific LLM model or tool involved
- Document the trigger condition

### 4. Review Regularly
- Check ENVIRONMENT.md before attempting similar work
- USE-CATALYST will remind you of relevant patterns

---

## Future Enhancements

1. **Pattern matching:** Automatically suggest relevant patterns before attempting work
2. **Pattern voting:** Rate usefulness of patterns for prioritization
3. **Pattern expiration:** Mark outdated patterns as deprecated
4. **Cross-project patterns:** Share patterns across different repositories

---

*This system ensures every mistake becomes a learning opportunity, making the agent progressively smarter with each session.*