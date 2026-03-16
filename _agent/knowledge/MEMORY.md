# Memory and Lessons Learned

## Configuration-Based Prompt Templates Implementation

### Key Learning:
When implementing custom summary prompts from config.yaml, the main challenge was properly distinguishing between file references (strings starting with `./`, `/` or containing path separators) and direct text content. This is crucial for proper functionality.

File-based references should be identified by strings that start with "./", "/", or contain path separators to be treated as paths.
Direct text content that doesn't match these patterns should be used literally as prompt templates in config files.

### Implementation Pattern:
This pattern of distinguishing between path strings and direct text can be applied to similar future features where configuration values may represent either file references or literal values, maintaining backward compatibility while enabling new functionality.

The solution involves checking the string prefix/syntax before deciding how to handle it during loading.