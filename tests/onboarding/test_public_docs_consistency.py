from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

import keeprollming
from keeprollming.filters.multimodal_validator.config import SCHEMA as MULTIMODAL_SCHEMA
from keeprollming.overrides import ALLOWED_OVERRIDE_KEYS

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_and_package_metadata_versions_match():
    with (ROOT / "pyproject.toml").open("rb") as project_file:
        project_metadata = tomllib.load(project_file)

    version = project_metadata["project"]["version"]
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert keeprollming.__version__ == version
    assert re.search(rf"^## v{re.escape(version)}(?: — unreleased)?$", changelog, re.MULTILINE)


def test_readme_documentation_links_exist():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    document_links = re.findall(r"\]\((docs/[^)#]+\.md)(?:#[^)]*)?\)", readme)

    assert "docs/ROUTING_AND_CONCURRENCY.md" in document_links
    assert "docs/API_REFERENCE.md" in document_links
    assert "docs/CLIENT_INTEGRATION.md" in document_links
    assert "docs/CONCEPTS.md" in document_links
    assert all((ROOT / link).is_file() for link in document_links)


def test_every_supported_override_is_documented_and_demonstrated():
    config = yaml.safe_load((ROOT / "config.example.full.yaml").read_text(encoding="utf-8"))
    documented = (ROOT / "docs/CONFIGURATION.md").read_text(encoding="utf-8").split("## Route request overrides", 1)[1]
    examples = {key for route in config["routes"].values() for key in route.get("overrides", {})}
    assert ALLOWED_OVERRIDE_KEYS <= examples
    assert all(f"`{key}`" in documented for key in ALLOWED_OVERRIDE_KEYS)


def test_multimodal_settings_and_capture_examples_cover_current_contract():
    config = yaml.safe_load((ROOT / "config.example.full.yaml").read_text(encoding="utf-8"))
    example = config["routes"]["filters/multimodal"]["filters"]["multimodal_validator"]
    assert set(MULTIMODAL_SCHEMA.fields) <= set(example)
    assert config["request_capture"]["policy"] == "disabled"
    assert config["body_capture"]["policy"] in {"errors_only", "disabled"}
    docs = (ROOT / "docs/CONFIGURATION.md").read_text(encoding="utf-8")
    assert "before request" in docs and "exact final HTTP body" in docs


def test_release_routing_options_have_complete_examples():
    config = yaml.safe_load((ROOT / "config.example.full.yaml").read_text(encoding="utf-8"))
    shared = config["routes"]["code/shared-worker"]
    assert shared["max_concurrent"] == 4
    assert shared["allow_idle_reservation"] is False
    policy = config["routes"]["public/code"]["balance"]
    assert policy["selection_policy"] == "least_loaded"
    assert policy["promotion"]["expected_current_completion_tokens"] == 1024


def test_upgrade_notes_are_linked_and_explain_behavior_changes():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/UPGRADING.md" in readme
    notes = (ROOT / "docs/UPGRADING.md").read_text(encoding="utf-8")
    for setting in (
        "max_tokens",
        "4096",
        "lookahead_turns",
        "allow_idle_reservation",
        "--stop-timeout",
        "client_access",
    ):
        assert setting in notes
