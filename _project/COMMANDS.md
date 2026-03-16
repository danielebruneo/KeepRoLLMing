# Commands

## Development & Testing
- `python -m venv .venv` - Create virtual environment
- `source .venv/bin/activate` - Activate virtual environment  
- `pip install -r requirements.txt` - Install production dependencies
- `pip install -r requirements-dev.txt` - Install development dependencies

## Running the Application
- `uvicorn keeprollming.app:app --host 0.0.0.0 --port 8000` - Start the FastAPI server
- `export UPSTREAM_BASE_URL="http://127.0.0.1:1234"` - Set upstream base URL

## Testing
- `pytest` - Run all tests using pytest
- `./scripts/run-tests.sh` - Run all tests in serial mode 
- `./scripts/run-single-test.sh` - Run a single test
- `./scripts/run-parallel-tests.sh` - Run tests in parallel mode
- `./scripts/run-curl-tests.sh` - Run curl-based integration tests

## Setup & Configuration
- `./scripts/setup.sh` - Run setup script for project initialization
- `./scripts/set-tests-venv.sh` - Set up test environment