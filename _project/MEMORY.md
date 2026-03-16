# Memory

Use this file for non-obvious lessons that are likely to matter again.

## Template Reference
This file follows the [MEMORY.template.md](../_templates/MEMORY.template.md) format for consistency.

## Topic: Template utilization in documentation
- Date/session: 15/03/2026 10:21:59
- Lesson: When creating new documentation elements, ensure they follow the established template format and cross-reference related components properly.
- Relevant files: _templates/ACTIVE_TASK.template.md, _docs/development/WORKFLOW.md
- Category: Documentation Pattern

## Topic: Symlink handling in skill directories
- Date/session: 15/03/2026 12:47:00
- Lesson: When working with skills in CATALYST projects, be aware that some files like SKILL-FEEDBACK.md are symlinks to SKILL.md. Only edit the actual content file
 (SKILL.md) as both links point to the same content. Always run 'ls -la' first to check for symlink relationships before making any modifications.
- Relevant files: _skills/FEEDBACK/SKILL-FEEDBACK.md, _skills/FEEDBACK/SKILL.md
- Category: System Pattern

## Topic: Skill integration decision-making
- Date/session: 15/03/2026 14:30:00
- Lesson: When multiple skills are involved in a task, evaluate whether they complement each other or conflict. Consider the skill's primary purpose and how it inte
racts with others.
- Relevant files: _skills/FEEDBACK/SKILL-FEEDBACK.md, _skills/IMPROVE-SKILLS/SKILL-IMPROVE-SKILLS.md
- Category: Decision Pattern

## Topic: Workflow efficiency analysis
- Date/session: 15/03/2026 15:15:00
- Lesson: Identify bottlenecks in task execution by analyzing time spent on different activities. Look for patterns where the same tasks are performed repeatedly wi
th similar inefficiencies.
- Relevant files: _skills/FEEDBACK/SKILL-FEEDBACK.md, _agent/HANDOFF.md
- Category: Process Pattern

## Topic: Comprehensive API documentation approach
- Date/session: 15/03/2026 14:30:00
- Lesson: When documenting API endpoints, it's important to cover all parameters including optional ones, response formats for both streaming and non-streaming mode
s, usage examples with different profiles, and environment variables that affect behavior. The documentation should be comprehensive enough for developers to unders
tand how to use the system effectively.
- Relevant files: _docs/API_DOCUMENTATION.md, README.md
- Category: Documentation Pattern

## Topic: Bootstrap redundancy for agent entrypoints
- Date/session: 15/03/2026 14:50:00
- Lesson: Treat QWEN.md as the runner-specific bootstrap loader, AGENTS.md as the canonical workflow, and README.md as human-facing redundancy. Keeping all three al
igned makes CATALYST more resilient when runner-specific files are regenerated.
- Relevant files: QWEN.md, AGENTS.md, README.md
- Category: System Pattern

## Topic: Runtime boundary for tool schemas
- Date/session: 15/03/2026 14:52:00
- Lesson: Repository documentation should not redefine runtime tool schemas. When local docs tried to document tool parameters, agent behavior regressed. Keep workf
low guidance in repo docs, and leave tool contracts to the runtime.
- Relevant files: AGENTS.md, QWEN.md
- Category: System Pattern

## Topic: Script organization best practices
- Date/session: 15/03/2026 15:47:00
- Lesson: All script files should be placed in the dedicated scripts/ directory for proper organization and maintainability. This follows CATALYST's established con
ventions for keeping project tools properly grouped.
- Relevant files: scripts/export_catalyst.sh, scripts/export_project.sh
- Category: Process Pattern

## Topic: Documentation consistency lessons from WORK skill
- Date/session: 15/03/2026 15:47:00
- Lesson: When implementing documentation tasks using the WORK skill, it's important to ensure all new sections maintain exact consistency with existing template formats including spacing and structural elements. This prevents formatting inconsistencies that could confuse users or break cross-references.
- Relevant files: _docs/TROUBLESHOOTING.md, README.md, _agent/ACTIVE_TASK.md
- Category: Documentation Pattern

## Topic: Task status management improvements
- Date/session: 15/03/2026 15:47:00
- Lesson: The WORK skill effectively handles task cycles from pickup through completion but needs better logic for distinguishing between truly completed tasks vs. those still in progress to avoid redundant task pickups.
- Relevant files: _agent/HANDOFF.md, _agent/ACTIVE_TASK.md
- Category: Process Pattern

## Topic: Documentation integration patterns
- Date/session: 15/03/2026 15:47:00
- Lesson: When adding new documentation files, ensure proper integration with existing structures including:
  - Cross-reference links work correctly
  - Consistent formatting throughout
  - Proper datetime tracking (DD/MM/YYYY HH:MM:SS) maintained
- Relevant files: _docs/TROUBLESHOOTING.md, README.md, _agent/HANDOFF.md
- Category: Documentation Pattern

## Topic: Improvement opportunities from feedback analysis
- Date/session: 15/03/2026 15:47:00
- Lesson: Several improvement opportunities identified for future work:
  - Template Standardization: Create a more formal template validation process to check all new documentation against established formats before committing.
  - Task Status Logic: Enhance the WORK skill decision logic to better detect task completion status versus ongoing work.
  - Automated Lesson Capture: Implement systematic capture of lessons learned from completed tasks into agent memory without manual intervention.
- Relevant files: _agent/MEMORY.md, _skills/WORK/SKILL.md
- Category: Process Pattern

## Topic: Memory management best practices from feedback analysis
- Date/session: 15/03/2026 15:47:00
- Lesson: When updating agent memory files like _agent/MEMORY.md, always prefer incremental additions over complete file replacements to preserve existing knowledge. Use append or update methods instead of write_file to overwrite entire contents.
- Relevant files: _agent/MEMORY.md
- Category: Process Pattern

## Topic: Learning report handling patterns
- Date/session: 15/03/2026 16:38:44
- Lesson: All learning reports in _agent/learning_reports/ directory should be preserved as historical documentation rather than replaced. Each session creates a new timestamped file instead of updating existing ones to maintain complete knowledge base.
- Relevant files: _agent/learning_reports/
- Category: Process Pattern

## Topic: THINK before skill selection
- Date/session: 15/03/2026 17:24:16
- Lesson: When multiple skills could apply or the next action is unclear, explicitly route through THINK before executing. This reduces premature skill usage and scope drift.
- Relevant files: _skills/THINK/SKILL.md, _docs/development/WORKFLOW.md
- Category: Process Pattern

## Topic: ADAPT must stay small and local
- Date/session: 15/03/2026 17:24:16
- Lesson: ADAPT is powerful but risky. It should only implement minimal, low-risk changes to CATALYST workflow artifacts or a single skill. Large refactors should remain proposals or TODOs.
- Relevant files: _skills/ADAPT/SKILL.md, _skills/LEARN/SKILL.md
- Category: Process Pattern


## Topic: Scope-allowed CATALYST adaptation
- Date/session: 15/03/2026 20:44:21
- Lesson: When current scope is `CATALYST` or `META`, improvements to CATALYST skills, workflow docs, and knowledge files are valid repository changes. Do not misclassify them as "only agent capability improvements" with no actionable file target. In those cases prefer FEEDBACK -> THINK -> ADAPT with a minimal target.
- Relevant files: _skills/FEEDBACK/SKILL.md, _skills/THINK/SKILL.md, _skills/ADAPT/SKILL.md, AGENTS.md
- Category: Process Pattern

## Topic: Git directory handling and manual commit requirements
- Date/session: 16/03/2026 
- Lesson: When working with directories in CATALYST migration processes:
  - Empty directories are not tracked by git unless files are added to them  
  - Complex iterative operations may require manual intervention for proper commit tracking
  - Files within directories that are initially untracked need explicit add commands before committing
  - Process validation steps should include verification of all directory structures before final commits
- Relevant files: _agent/knowledge/, _project/
- Category: Process Pattern

## Topic: Iterative correction workflow in migration processes
- Date/session: 16/03/2026  
- Lesson: Migration tasks that involve semantic analysis and classification require iterative corrections:
  - Initial attempts may misclassify content 
  - Multiple rounds of review and correction are needed for proper placement
  - Complex cases sometimes need explicit manual steps to ensure correctness
  - Feedback cycles help improve process understanding for future executions
- Relevant files: _agent/knowledge/SKILL_PROPOSAL.md, _project/KNOWLEDGE_BASE.md, _agent/state/
- Category: Process Pattern