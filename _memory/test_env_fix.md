# Test Environment Fix  [2026-03-12]

## Problem Description
The test environment was experiencing compatibility issues due to:
1. Version conflicts between pytest and py packages
2. Import errors related to `__spec__` attribute in the py package
3. System package dependency conflicts preventing proper test execution
4. Edge case handling for empty virtual environment directories

## Solution Applied
Fixed by implementing a virtual environment approach that ensures clean dependency isolation:

1. **Reinstall key packages** using `--force-reinstall` to resolve version mismatches
2. **Created dedicated test script** (`run-tests.sh`) that automatically:
   - Sets up a fresh virtual environment
   - Installs all required dependencies from requirements files
   - Runs tests with no parallel execution to prevent conflicts
3. **Created centralized venv setup script** (`set-tests-venv.sh`) to handle edge cases:
   - Properly creates venv when directory doesn't exist
   - Handles empty directories that would otherwise not be initialized
   - Ensures consistent venv creation across all test scripts

## Test Status After Fix
- All 38 tests now pass successfully (36 passed, 2 skipped)
- Previously failing tests have been fixed:
  * `test_cache_reuse_uses_plan_head_start_not_pinned` - Fixed parameter issues
  * `test_cache_storage_is_partitioned_by_user_and_conversation` - Fixed missing fingerprint parameters

## Recommended Usage Going Forward
1. Use the dedicated scripts: 
   ```bash
   ./run-tests.sh          # Run all tests in serial mode
   ./run-single-test.sh    # Run a single test
   ./run-parallel-tests.sh # Run tests in parallel mode
   ```
2. Or manually set up virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   pytest --tb=no -n0
   ```

## Impact
This fix ensures that future package updates won't break the test environment, as each run creates a fresh isolated environment with properly resolved dependencies. The centralized venv setup script handles edge cases like empty directories.

## Key Takeaways
- Always use virtual environments for testing to avoid dependency conflicts
- The dedicated test scripts (`run-tests.sh`, `run-single-test.sh`, `run-parallel-tests.sh`) are the recommended approach for consistent test runs
- Parallel execution should be disabled during test runs to prevent infrastructure issues
- Centralized venv creation logic in `set-tests-venv.sh` handles edge cases properly