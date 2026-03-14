# Migration Notes

This package introduces a new split between:

- **human-facing docs** in `_docs/`
- **agent-facing operational knowledge** in `_agent/`
- **reusable project skills** in `_skills/`

## Suggested migration flow

1. Copy these files into the project root.
2. Keep old docs in place temporarily.
3. Run or manually apply:
   - `INIT-KNOWLEDGE-BASE`
   - `BUILD-REPO-MAP`
   - `SYNC-COMMANDS`
   - `REVIEW-DOCS`
4. Compare old structure vs new structure.
5. Only after review, run the cleanup script.

## Recommended mapping from old structure

- `_tasks/*` → `_agent/*`
- `_memory/*` → split between `_agent/MEMORY.md` and `_docs/decisions/DECISIONS.md`
- `_project/*` → `_docs/architecture/*` and `_docs/development/*`
- old ad-hoc skill docs → normalize into `_skills/<NAME>/`

## Important
The cleanup script is conservative but still destructive. Review before running it.
