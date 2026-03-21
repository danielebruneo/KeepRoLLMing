# Memory and Lessons Learned

## Terminal Handling for Interactive Dashboards

### Key Learning:
When implementing interactive terminal UIs with key bindings, raw terminal mode must be applied **temporarily within the main loop** (not in a background thread) to preserve `Ctrl+C` signal handling.

**Why Background Thread Failed:** Setting terminal mode in a background thread conflicts with the main loop's keyboard interrupt handling, breaking `Ctrl+C`.

**Working Pattern:**
1. Save original terminal settings before entering loop
2. Apply non-canonical mode (`ICANON`, `ECHO` disabled) temporarily
3. Use `os.read(fd, 1)` with `select.select()` for non-blocking key capture
4. Always restore original settings in `finally` block

This ensures:
- Key presses captured immediately without waiting for Enter
- `Ctrl+C` still works (terminal signals aren't disabled)
- Terminal returns to normal state on exit

### Implementation Pattern:
```python
fd = sys.stdin.fileno()
old_settings = termios.tcgetattr(fd)
try:
    new_settings = termios.tcgetattr(fd)
    new_settings[3] &= ~(termios.ECHO | termios.ICANON)
    new_settings[6][termios.VMIN] = 0
    new_settings[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSADRAIN, new_settings)
    
    try:
        while True:
            # render() + key capture with select()
    except KeyboardInterrupt:
        print("\n\n👋 Dashboard stopped.")
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
```

This pattern applies to any terminal UI requiring both real-time key capture and signal handling.

## Configuration-Based Prompt Templates Implementation

### Key Learning:
When implementing custom summary prompts from config.yaml, the main challenge was properly distinguishing between file references (strings starting with `./`, `/` or containing path separators) and direct text content. This is crucial for proper functionality.

File-based references should be identified by strings that start with "./", "/", or contain path separators to be treated as paths.
Direct text content that doesn't match these patterns should be used literally as prompt templates in config files.

### Implementation Pattern:
This pattern of distinguishing between path strings and direct text can be applied to similar future features where configuration values may represent either file references or literal values, maintaining backward compatibility while enabling new functionality.

The solution involves checking the string prefix/syntax before deciding how to handle it during loading.

## Skill Structure Consistency

### Key Learning:
All CATALYST skills must follow a consistent directory structure with:
1. A main documentation file named `SKILL-NAME.md`
2. A symlink named `SKILL.md` pointing to this main file
3. This pattern ensures proper recognition by the agent system during sync operations

### Implementation Pattern:
When creating or modifying skills, always ensure both files exist and are properly linked with correct relative paths.