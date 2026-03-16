# Constraints

## Scope discipline
- Solve only the requested problem.
- Do not perform opportunistic cleanup.
- Do not replace implementations just because another design seems nicer.

## Edit discipline
- Prefer modifying existing files over introducing new abstractions.
- Avoid cross-module refactors unless the problem clearly spans modules.
- Keep patches small and reviewable.

## Test discipline
- Never declare completion without relevant validation.
- Prefer targeted checks first.
- If a test is changed, explain whether behavior changed or only the expectation changed.

## Documentation discipline
- Update [_agent/HANDOFF.md](HANDOFF.md) after each meaningful work session.
- Add reusable lessons to [_agent/MEMORY.md](MEMORY.md).

## Runtime discipline
- Follow runtime tool schemas as provided by the environment.
- Do not redefine tool contracts inside repository docs.

## Template Discipline
- All new documentation files should follow established template formats for consistency.
- Reference relevant templates in each documentation file (e.g., [ACTIVE_TASK.template.md](../_templates/ACTIVE_TASK.template.md)).
- Ensure cross-referencing between related documentation elements and skills.