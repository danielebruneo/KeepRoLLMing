# Architectural Invariants

These are the rules that should remain true unless the user explicitly decides to change them.

## Template invariants
- The project boundary must remain clear.
- External compatibility is usually more important than internal elegance.
- Optional capabilities should fail soft instead of fail hard when possible.
- Logging/metrics must not break core request serving.
- Human docs and agent docs must stay separate.
- Task workflow must remain explicit and inspectable.

## Project-specific invariants
Add project-specific invariants here once the knowledge base is initialized.
