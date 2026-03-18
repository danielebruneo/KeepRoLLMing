import os
import sys
from pathlib import Path

# Set environment variable to use test config before any imports
TEST_CONFIG_PATH = Path(__file__).parent / "config.yaml"
os.environ["CONFIG_FILE"] = str(TEST_CONFIG_PATH)

# Ensure project root is importable when pytest chooses a different rootdir.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
