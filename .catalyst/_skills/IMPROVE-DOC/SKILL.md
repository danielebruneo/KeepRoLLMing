# IMPROVE-DOC

This skill helps extend and improve documentation in the ReBadge.API project where it makes sense.

## Purpose
To intelligently identify opportunities to enhance, consolidate, or add content to existing documentation files based on:
1. Missing information that should be documented
2. Opportunities for better organization and cross-referencing
3. Gaps between code implementation and documentation coverage
4. Areas where additional context would improve understanding

## When to Use
This skill is appropriate when:
- A new feature or component has been implemented but lacks documentation
- Documentation files need consolidation or restructuring
- There are opportunities to add missing cross-references between documents
- Code changes require corresponding documentation updates
- The project needs better alignment between implementation and documentation

## Decision Framework

### 1. Identify Documentation Gaps
Before extending documentation, determine what information is missing:
- **New Features**: Has the new functionality been documented?
- **Code Changes**: Do code modifications need explanatory notes?
- **Architecture Updates**: Have architectural changes been communicated in docs?
- **Configuration Details**: Are there undocumented configuration options or settings?

### 2. Determine Best Location for Content
Identify where the documentation should be placed:
- **Project Overview** (`_project/_docs/project_overview.md`): High-level architecture and key components
- **API Documentation** (`_project/_docs/api_endpoints.md`): REST API endpoint details
- **Technical Details** (`_project/_docs/anticopy_system.md`, `carddump_enhancements.md`): Implementation specifics
- **Knowledge Base** (`_project/KNOWLEDGE_BASE.md`): Task management and workflow information

### 3. Assess Consolidation Opportunities
Evaluate whether content should be consolidated:
- Can duplicate information across files be merged?
- Should related topics be grouped together for better organization?
- Is there redundant documentation that can be streamlined?

## Implementation Guidelines

### Extending Existing Files
When extending existing documentation files:
1. **Add Timestamp**: Include "Last updated" in DD/MM/YYYY HH:MM:SS format at the top
2. **Maintain Structure**: Follow existing file structure and formatting conventions
3. **Cross-Reference**: Link to related documents where appropriate
4. **Be Specific**: Add targeted, relevant content rather than generic information

### Creating New Files
When creating new documentation files:
1. **Follow Naming Convention**: Use descriptive names like `feature_name.md` or `component_name.md`
2. **Include Timestamp**: Add "Last updated" at the top of each file
3. **Maintain Consistency**: Match formatting and structure with existing docs in `_project/_docs/`
4. **Add Cross-References**: Link to related documentation files

### Improving Existing Files
When improving existing documentation:
1. **Enhance Clarity**: Improve readability and organization
2. **Add Missing Sections**: Include sections that are typically expected (e.g., Usage, Configuration)
3. **Update References**: Ensure all links and references point to current locations
4. **Consolidate Content**: Merge duplicate or related information where appropriate

## Documentation Categories

### User-Facing Documentation
- API endpoints and their usage
- Configuration options and settings
- Common procedures and workflows
- Troubleshooting guides

### Technical Documentation
- Architecture details and component descriptions
- Implementation specifics and patterns
- Code structure and organization
- Database schema changes

### Workflow Documentation
- Task management protocols (CATALYST workflow)
- Agent behavior guidelines
- Project-specific conventions
- Best practices for development

## Integration Points

This skill integrates with:
1. **Task Management**: When documentation improvements are part of a larger task, ensure proper tracking via `_agent/state/TODOS.md`
2. **Knowledge Base**: Cross-reference with `_project/KNOWLEDGE_BASE.md` for project context
3. **Repository Map**: Update `_project/MAP.md` when new files or significant changes are made

## Example Scenarios

### Scenario 1: New Feature Documentation
**Context**: A new API endpoint has been implemented
**Action**: 
- Create a new file in `_project/_docs/` with the endpoint details
- Add timestamp and follow existing format
- Cross-reference from main API documentation file

### Scenario 2: Architecture Update
**Context**: The security system architecture has changed
**Action**:
- Update relevant sections in `anticopy_system.md` or `project_overview.md`
- Add timestamp to track the change
- Ensure all references are updated

### Scenario 3: Missing Configuration Details
**Context**: A configuration option exists but isn't documented
**Action**:
- Add a section explaining the option in the appropriate documentation file
- Include examples and best practices
- Cross-reference related files if needed

## Constraints
1. **Don't Over-document**: Only add information that provides value to readers
2. **Maintain Consistency**: Follow existing formatting and structure conventions
3. **Be Specific**: Add targeted, relevant content rather than generic information
4. **Track Changes**: Always include timestamps to track when documentation was last updated

## Related Skills
- `PICKUP-TASK`: When documentation improvements are part of a larger task
- `CLOSE-TASK`: To mark completion after documentation has been extended
- `UPDATE-KNOWLEDGE-BASE`: For updating the project knowledge base

This skill ensures that documentation remains current, comprehensive, and well-organized as the ReBadge.API project evolves.
