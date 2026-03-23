# CONSOLIDATE-DOC

This skill helps consolidate documentation in the ReBadge.API project by identifying and removing duplicates, organizing content logically, and avoiding unnecessary fragmentation.

## Purpose
To systematically review existing documentation and:
1. Identify duplicate or redundant information across files
2. Merge overlapping content into single authoritative sources
3. Consolidate related topics that should be grouped together
4. Remove obsolete or outdated documentation sections
5. Create new organized structures when needed for complex subjects

## When to Use
This skill is appropriate when:
- Multiple files contain the same information about a topic
- Documentation has become fragmented over time with scattered details
- Related concepts are documented in separate files unnecessarily
- There's redundancy between different documentation sections
- The project needs better organization and maintainability of docs

## Decision Framework

### 1. Identify Duplicate Content
Before consolidating, identify what is duplicated:
- **Same Information**: Identical or near-identical content across multiple files
- **Overlapping Coverage**: Different files covering the same aspect from different angles
- **Redundant Explanations**: Multiple explanations of the same concept

### 2. Determine Consolidation Strategy
Choose the appropriate approach based on the situation:
- **Merge**: Combine two similar sections into one authoritative source
- **Redirect**: Keep original, remove duplicate and add cross-reference
- **Split**: Separate related but distinct topics into different files
- **Archive**: Move obsolete content to a separate archive section

### 3. Assess File Structure
Evaluate whether the current file organization makes sense:
- Are related topics in the same file?
- Is there unnecessary fragmentation?
- Do some files contain too much or too little content?

## Implementation Guidelines

### Identifying Duplicates
When reviewing documentation, look for:
1. **Exact Matches**: Same text appearing in multiple files
2. **Semantic Equivalence**: Different wording but same meaning
3. **Cross-file References**: Multiple references to the same concept
4. **Version Conflicts**: Different versions of the same information

### Consolidation Approaches

#### Merge Strategy
When two sections cover similar ground:
1. Identify which file is more authoritative or complete
2. Move content from less authoritative to more authoritative
3. Update cross-references in both files
4. Remove duplicate section from original location
5. Add timestamp to track the consolidation

#### Split Strategy
When related topics should be separate:
1. Identify natural boundaries between topics
2. Create new file for the split topic
3. Move content appropriately
4. Update all cross-references
5. Ensure no information is lost in the process

### Creating New Organized Files
When creating new consolidated files:
1. **Clear Purpose**: Define what this file will cover
2. **Single Topic Focus**: One main subject per file
3. **Comprehensive Coverage**: Include all relevant sub-topics
4. **No Duplication**: Ensure no overlap with existing files

## Documentation Categories for Consolidation

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

## Consolidation Patterns

### Pattern 1: Merge Overlapping Sections
**Situation**: Two files contain similar information about the same topic
**Action**: 
1. Identify which file is more complete/authoritative
2. Move content from less authoritative to more authoritative
3. Update cross-references in both locations
4. Remove duplicate section

### Pattern 2: Split Related Topics
**Situation**: One file contains multiple unrelated topics
**Action**:
1. Identify natural topic boundaries
2. Create new files for each distinct topic
3. Move content appropriately
4. Update all cross-references

### Pattern 3: Consolidate Configuration Details
**Situation**: Configuration information is scattered across multiple files
**Action**:
1. Gather all configuration-related content
2. Create a centralized configuration documentation file
3. Remove or redirect references from other locations
4. Add examples and best practices

### Pattern 4: Archive Obsolete Content
**Situation**: Documentation contains outdated or superseded information
**Action**:
1. Identify obsolete sections (check timestamps, version numbers)
2. Move to archive section or separate file
3. Update references to point to current content
4. Mark archived content clearly

## Integration Points

This skill integrates with:
1. **IMPROVE-DOC**: When consolidation is part of documentation improvement efforts
2. **Task Management**: Track consolidation tasks via `_agent/state/TODOS.md`
3. **Knowledge Base**: Update `_project/KNOWLEDGE_BASE.md` after major consolidations

## Example Scenarios

### Scenario 1: Duplicate API Documentation
**Situation**: API endpoints are documented in both `api_endpoints.md` and multiple individual endpoint files
**Action**: 
- Consolidate all endpoint details into a single comprehensive file
- Remove duplicate entries from other locations
- Add cross-references where appropriate

### Scenario 2: Scattered Configuration Info
**Situation**: Configuration options for the security system are documented in multiple places
**Action**:
- Create a centralized `configuration.md` file
- Move all configuration-related content there
- Update references to point to new location

### Scenario 3: Overlapping Architecture Docs
**Situation**: System architecture is described in both `project_overview.md` and `anticopy_system.md`
**Action**:
- Identify overlapping sections
- Merge into single authoritative source
- Remove duplicates from other files
- Update cross-references

## Constraints
1. **Preserve Information**: Never lose information during consolidation; archive if needed
2. **Maintain Context**: Keep historical context where important for understanding evolution
3. **Update References**: Always update all references when moving content
4. **Track Changes**: Document what was consolidated and why in a changelog or notes file

## Related Skills
- `IMPROVE-DOC`: For general documentation improvements including consolidation
- `PICKUP-TASK`: When consolidation is part of a larger task
- `CLOSE-TASK`: To mark completion after consolidation work is done

This skill ensures that documentation remains organized, maintainable, and free from unnecessary duplication as the ReBadge.API project grows.
