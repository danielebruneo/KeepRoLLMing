#!/bin/bash
# scripts/clear-cache.sh
# Clear all Python bytecode caches and __pycache__ directories

set -e

cd "$(dirname "$0")/.."

echo "Clearing Python caches..."

# Remove all __pycache__ directories
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null

# Remove individual .pyc files
find . -type f -name "*.pyc" -delete 2>/dev/null
find . -type f -name "*.pyo" -delete 2>/dev/null

echo "Done. Restart the server with:"
echo "  nohup scripts/start-live-server.sh --port 8000"
