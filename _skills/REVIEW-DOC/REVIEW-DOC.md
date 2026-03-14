# REVIEW-DOC Skill

## Description

This skill reviews all markdown files in the project to ensure they accurately reflect the current status of the project and contain only relevant information. It consolidates knowledge by identifying missing information and moving irrelevant content to appropriate locations.

## Usage

```
/skills REVIEW-DOC
```

## Functionality

1. **Status Verification**: 
   - First of all retrieve the list of all .md files in the project, just filter out what's unrelevant to the project (i.e. external dependencies, conversation md files, etc..)
   - Reviews all relevant .md files for accuracy
   - Understand how md files are structured in the project
   - Understand role of WORKFLOW, MEMORY and PROJECT files in particular
   - Identifies outdated or incorrect information
   - Fixes documentation to match actual implementation

2. **Content Consolidation**:
   - Checks if all relevant information is present
   - Moves irrelevant content to appropriate locations
   - Ensures each document contains only information relevant to its purpose
   - If you need, create new .md files to better organize content or store missing relevant content
   - Consolidates knowledge across documents
   - Makes sure documents are in the correct location

3. **Documentation Quality**:
   - Ensures documentation reflects actual codebase state
   - Identifies missing information that should be documented
   - Maintains consistency across all project documentation
   
3. **Date references**:
   - Make sure that every document and every task related section contains a Date reference [DD/MM/YYYY format].
   - If a piece of information misses Date reference, try to reconstruct it from git, if you are not able to get a reliable date insert something like "<[CURRENT_DATE]"

## Input Requirements

- Project directory with markdown files
- Access to all .md files in the repository

## Output

- Updated markdown files with corrected content
- Summary of changes made
- Report on every discrepancy you found and how you addressed it
- Identification of missing or irrelevant information

## Success
this task is accomplished if the following conditions are met:
- all the information are up-to-date and in the correct place in the project structure
- WORKFLOW related files reflect the current status of the project (i.e. close ACTIVE_TASK if has eben completed, archive it to COMPLETED_TASK, etc..)
- all the .md files that are relevant to the project contains at least one date reference
- that date is expressed in the format specified above
- any .md file that  have no date reference, is not strictly related to the project (i.e. external dependencies files, conversation files, etc..)
  
## Notes

This skill helps maintain high-quality, accurate documentation by ensuring that:
- All documentation reflects current implementation status
- Information is properly organized and consolidated
- Each document contains only relevant content
- Missing information is identified and addressed