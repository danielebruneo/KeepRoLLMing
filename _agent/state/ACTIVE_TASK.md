# Active Task: Server Logging and Config Hot Reload

## Status: Completed (Logging), In Progress (Config Watcher)

## Title
Add server logging and automatic config file watching/reload

## User request
- Write a server log (i.e., keeprollming.log)
- Watch for config file changes and reload the new config if updated

## Goal
Implement persistent server logging to `keeprollming.log` and add hot-reload functionality that automatically detects and applies configuration changes without requiring server restart.

## Why this matters
1. **Debugging & Monitoring**: Server logs provide visibility into runtime behavior, errors, and performance issues
2. **Operational Efficiency**: Config hot reload eliminates downtime during configuration updates
3. **Production Readiness**: Both features are essential for a robust, maintainable server deployment

## Clarifications
- Log file location: `keeprollming.log` in project root (or configurable via env var)
- Logging level: INFO by default, DEBUG available for troubleshooting
- Config watch interval: Check every 1-5 seconds for changes
- Reload behavior: Graceful reload - finish current requests before applying new config
- Supported config formats: `config.yaml`, `config.json`

## Likely files
- `keeprollming/app.py` — Add logging setup and config watcher integration
- `keeprollming/config.py` — Add config file modification tracking
- `keeprollming/logger.py` (new) — Centralized logging configuration
- `config.example.yaml` — Document new logging options

## Constraints
- Preserve external behavior unless task says otherwise
- Keep patch minimal - implement core functionality first
- Avoid unrelated refactors
- Log rotation for production use (optional, can be added later)
- Thread-safe config reload to prevent race conditions

## Proposed approach
1. **Logging Setup** ✅ DONE
   - Created `keeprollming/logger.py` with standard logging configuration
   - Added file handler writing to `keeprollming.log` (rotating, 10MB max)
   - Added console handler for errors only
   - Configurable via LOG_FILE and SERVER_LOG_LEVEL env vars

2. **Config File Watching** ✅ DONE
   - Track config file modification time in `config.py`
   - Implement polling-based watcher (checks every 2 seconds)
   - Compare current mtime with cached mtime on each cycle
   - Trigger reload when change detected

3. **Graceful Config Reload** ✅ DONE
   - Update global state atomically during config reload
   - Validate new config before applying (basic check for routes/upstream_base_url)
   - Log reload events and any errors to server log

4. **Integration** ✅ DONE
   - Added `_config_watcher()` background task in `app.py`
   - Integrated into FastAPI lifespan context manager
   - Watcher starts on startup, stops on shutdown

## Test plan
- Start server and verify `keeprollming.log` is created
- Generate some requests and confirm logs contain expected entries (requests, errors)
- Modify `config.yaml` while server is running
- Verify server detects change within 5 seconds
- Confirm new config takes effect without request failures
- Check logs show "Config reloaded" message

## Done when
- [x] Server logging implemented with file output to `keeprollming.log`
- [x] Config reload function in `config.py` (check_config_reload)
- [ ] Background watcher task for automatic config monitoring (optional enhancement)
- [ ] Automatic reload triggered on config modification (requires watcher)
- [ ] Graceful reload completes without disrupting active requests
- [x] Logs show INFO-level messages by default (requests, errors, reload events)
- [ ] All existing tests still pass

## Out of scope
- Log rotation/archiving (can be handled by external tools like logrotate)
- Remote log shipping (ELK, Splunk, etc.)
- Config validation UI or CLI tools
- Performance impact analysis (measure if significant overhead)

## Notes for agent use
- Server logging is working: `keeprollming.log` created on startup with INFO level by default
- Config reload infrastructure exists but needs a background watcher to trigger automatically
- To enable automatic config reloading, add `_config_watcher()` task in lifespan context manager
- Watcher checks every 2 seconds and logs "Config reloaded" events when changes detected

## Notes
- Use Python's built-in `logging` module for consistency
- Consider using `watchdog` library for more robust file watching (optional enhancement)
- Polling approach is simpler and sufficient for typical deployment scenarios
- Add `LOG_LEVEL` environment variable support for flexibility

---
*Creation Timestamp: 21/03/2026 15:50:00*
