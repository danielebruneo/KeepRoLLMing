# ACTIVE TASK

## Status
- [ ] In progress

## Title
Implement support for custom summary prompts

## User Request
Implement support for custom summary prompts

## Goal
To add functionality that allows users to define and use custom prompts for summarization, improving the flexibility and control over how conversation summaries are generated.

## Why This Matters
This feature will allow users to customize how their conversations get summarized, making the system more adaptable to different use cases and requirements. It's a key enhancement for user experience and configurability.

## Clarifications
- Need to determine where in the codebase this functionality should be implemented
- Should support various prompt formats (JSON, text files, etc.)
- May need to consider how to store and manage these custom prompts

## Likely Files
- config.yaml (for configuration)
- main.py or orchestrator.py (core logic implementation) 
- README.md (documentation updates)

## Constraints
- Must maintain backward compatibility with existing summarization modes
- Should follow the project's current architecture patterns
- Implementation should be well-documented and tested

## Proposed Approach
1. Add a new configuration option for custom summary prompts in config.yaml
2. Implement logic to load and parse custom prompt configurations 
3. Update the summary generation process to use these custom prompts when enabled
4. Create documentation describing how to set up and use custom summary prompts

## Test Plan
- Verify that existing summarization modes still work correctly
- Test loading of different prompt formats (JSON/text)
- Confirm that custom prompts are properly applied during summary generation
- Ensure error handling for invalid prompt configurations

## Done When Criteria
- Custom summary prompt functionality is implemented and working
- All tests pass with both default and custom prompt configurations
- Documentation is updated to reflect the new feature

## Out of Scope
- Implementation of UI/CLI interfaces for managing prompts (will be handled separately)
- Adding support for dynamic prompt generation at runtime 
- Complex natural language processing enhancements beyond basic prompt configuration

## Notes for Agent Use
- This is a moderately complex enhancement that requires understanding of existing code architecture
- Follow the established patterns in the project for configuration handling and testing

## Creation Timestamp
16/03/2026 14:59:45
