"""Regression checks for the public clone-to-first-request documentation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("example", ["config.example.yaml", "config.example.full.yaml"])
def test_public_configuration_example_loads_through_the_real_loader(example: str) -> None:
    env = os.environ.copy()
    env["CONFIG_FILE"] = str(ROOT / example)
    result = subprocess.run(
        [sys.executable, "-c", "import keeprollming.config"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_file_backed_system_prompt_is_loaded_relative_to_config_file(tmp_path: Path) -> None:
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "architect.md").write_text(
        "You are the architecture reviewer.", encoding="utf-8"
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
routes:
  code/architect:
    model: test-model
    upstream_url: http://127.0.0.1:8080
    filters:
      system_prompt:
        enabled: true
        prompt_file: prompts/architect.md
""",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["CONFIG_FILE"] = str(config_path)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from keeprollming.config import USER_ROUTES; "
            "assert USER_ROUTES[0].filters['system_prompt']['prompt'] == "
            "'You are the architecture reviewer.'",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_public_shell_entrypoints_are_syntactically_valid() -> None:
    scripts = [
        "krm",
        "scripts/setup.sh",
        "scripts/set-tests-venv.sh",
        "scripts/run-single-test.sh",
        "scripts/start-with-fake.sh",
    ]
    for script in scripts:
        result = subprocess.run(
            ["bash", "-n", str(ROOT / script)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{script}: {result.stderr}"
