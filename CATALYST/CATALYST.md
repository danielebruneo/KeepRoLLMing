# CATALYST

This root file is a bootstrap shim for installed projects.

Canonical core docs live in:
- [.catalyst/CATALYST.md](.catalyst/CATALYST.md)
- [.catalyst/AGENTS.md](.catalyst/AGENTS.md)

## Setup / installation note
CATALYST is normally installed or updated by running:

```bash
./scripts/setup_catalyst.sh .
```

or from an external clone of the CATALYST repository:

```bash
/path/to/CATALYST/scripts/setup_catalyst.sh /path/to/project
```

The setup flow is deterministic and conservative:
- archive legacy assets first
- install the new layered core
- recreate `.qwen/skills/` as a clean runtime registry
- re-open the agent
- run `DIGEST-LEGACY-CATALYST` for semantic migration of legacy knowledge
