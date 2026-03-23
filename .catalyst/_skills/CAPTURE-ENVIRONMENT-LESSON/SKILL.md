---
name: CAPTURE-ENVIRONMENT-LESSON
description: Automatically saves environmental and tool-calling pattern learnings when initial approaches fail.
---

# CAPTURE-ENVIRONMENT-LESSON Skill

## Goal
Automatically capture and save environmental/LLM tool-calling patterns when initial approaches fail and a workaround is discovered.

## Purpose
This skill ensures agents learn from their mistakes by systematically capturing:
- **LLM Model Patterns:** Model-specific behaviors, quirks, and workarounds
- **Tool Calling Patterns:** Tool limitations and successful usage patterns
- **Environment Quirks:** Platform-specific issues and solutions

These patterns are saved to `_agent/knowledge/ENVIRONMENT.md` and referenced in `_agent/knowledge/MEMORY.md` to prevent repeated mistakes.

## Auto-Trigger Conditions
This skill should be automatically invoked when:
1. **First approach fails** with unexpected error
2. **Tool call fails** with error not in documentation
3. **Environment-specific limitation** discovered
4. **Workaround implemented successfully**

## Procedure

### 1. Capture Failure Context
Document:
- **Original approach:** What was tried first
- **Error received:** Exact error message or behavior
- **Expected vs actual:** What was hoped to happen vs what happened

### 2. Document Workaround
Document:
- **Successful approach:** The workaround that worked
- **Code example:** Working code snippet
- **Why it works:** Explanation of the fix
- **Limitations:** Any caveats or edge cases

### 3. Categorize Pattern
Determine category:
- **LLM Model Pattern** (OpenAI, Anthropic, etc.)
- **Tool Calling Pattern** (specific tool or API)
- **Environment Quirk** (platform, OS, runtime)

### 4. Save to ENVIRONMENT.md
Create structured entry:
```markdown
### [Category]: [Specific Issue]
- **Trigger:** When [condition]
- **Failed approach:** [what didn't work]
- **Error:** [exact error]
- **Workaround:** [working solution]
- **Code:**
  ```python
  [working code]
  ```
- **Why it works:** [explanation]
- **Related:** [file/skill references]
```

### 5. Update MEMORY.md
Add to "Known Issues & Solutions" section:
```
### Issue: [Brief description]
**Solution:** [summary of workaround]
```

### 6. Update HANDOFF.md (optional)
Add note: "Captured environment lesson: [pattern name]"

## When to Use
- **Immediate:** When workaround found during work
- **Session end:** Review all failures and save patterns
- **Before retry:** If attempting similar work later

## Constraints
- Always capture both failed and working approaches
- Include exact error messages when possible
- Add code examples for reproducibility
- Link to related files and skills
- Date-stamp all entries

## Example Usage

### Scenario: HTTPX Exception in Nested Async Generator

**Failed Approach:**
```python
async def _iter():
    try:
        response = await client.get(url)
    except httpx.ConnectError:  # NameError!
        handle_error()
```

**Error:**
```
NameError: name 'httpx' is not defined
```

**Workaround:**
```python
async def _iter():
    try:
        response = await client.get(url)
    except Exception as e:
        exc_type = sys.exc_info()[1].__class__
        if exc_type == httpx.ConnectError:
            handle_error()
```

**Captured Entry:**
```markdown
### LLM Model Pattern: Python Exception Handling in Nested Scopes
- **Trigger:** When catching exceptions in nested async generators
- **Failed approach:** `except httpx.ConnectError:` directly in nested scope
- **Error:** NameError: name 'httpx' is not defined
- **Workaround:** Use runtime exception type detection with `sys.exc_info()`
- **Code:**
  ```python
  except Exception as e:
      exc_type = sys.exc_info()[1].__class__
      if exc_type == httpx.ConnectError:
          handle_error()
  ```
- **Why it works:** `sys.exc_info()[1]` returns actual exception instance at runtime, bypassing static scope resolution
- **Related:** `_agent/knowledge/MEMORY.md#HTTPX-Exception-Handling`, `keeprollming/app.py`
```

## Related Skills
- **[REMEMBER]** - Save important learnings to memory
- **[DISTILL-LEARNINGS]** - Convert patterns to reusable lessons
- **[FEEDBACK]** - Analyze friction and identify patterns
- **[UPDATE-KNOWLEDGE-BASE]** - Add to project documentation

## Integration with Workflow
```
1. Attempt approach
2. Fails → Find workaround
3. CAPTURE-ENVIRONMENT-LESSON
   → Saved to ENVIRONMENT.md
   → Added to MEMORY.md
4. Future sessions check ENVIRONMENT.md
5. No repeated mistakes!
```

## DateTime Tracking
All entries include timestamp in format: `DD/MM/YYYY HH:MM:SS`

## Output Files
- `_agent/knowledge/ENVIRONMENT.md` - Primary pattern repository
- `_agent/knowledge/MEMORY.md` - Quick reference section
- `_agent/state/HANDOFF.md` - Optional session note