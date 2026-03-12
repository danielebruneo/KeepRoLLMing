# Fix test isolation and interdependence issues in test suite

## Task Description
Identify and resolve test isolation problems that cause `test_e2e_summary_cache_hit_reuses_previous_summary` to fail when run as part of the full test suite, while passing when run individually.

## Current Status
The test `test_e2e_summary_cache_hit_reuses_previous_summary` passes when run alone but fails when executed in the full test suite. This indicates test isolation issues where tests are interfering with each other's state or resources.

## Analysis
Based on testing results:
1. Test passes in isolation: `run-single-test.sh tests/e2e/test_http_e2e.py::test_e2e_summary_cache_hit_reuses_previous_summary[fake]`
2. Test fails in suite: `run-tests.sh` 
3. The test is marked as `non_parallelizable` which suggests it has shared state dependencies
4. Likely causes:
   - Shared cache directory between tests
   - Test fixtures that don't properly clean up resources
   - Test state contamination from previous tests

## Root Cause Investigation
The issue likely stems from:
- Cache files being created during one test and not properly cleaned up before the next test
- Test fixtures using shared resources without proper cleanup mechanisms
- The `non_parallelizable` marker indicating shared resource dependencies

## Approach
1. Investigate cache directory usage in tests
2. Examine how test fixtures handle cleanup of temporary resources
3. Check if cache entries from previous tests interfere with current test
4. Implement proper isolation mechanisms for the cache-dependent test

## Verification
- Test should pass both when run individually and as part of full test suite
- Cache directory should be properly isolated between tests
- No shared state contamination between tests

## Next Steps
1. Analyze test fixtures and their resource cleanup
2. Check how cache directories are managed during testing
3. Implement proper isolation for cache-dependent tests
4. Verify all tests pass in both isolated and suite execution modes