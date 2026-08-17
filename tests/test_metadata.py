"""Repository metadata, documentation, manifest, and discoverability parity tests for lock-master."""

import json
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_version_consistency():
    """Verify version consistency across VERSION, pyproject.toml, and ellmos-module.v2.json."""
    version_file = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert version_file == "1.5.1"

    with (ROOT / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)
    assert pyproject["project"]["version"] == "1.5.1"

    with (ROOT / "ellmos-module.v2.json").open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    assert manifest["version"] == "1.5.1"


def test_manifest_parity():
    """Verify that all ellmos-module manifests use authoritative ellmos-ai repository URLs."""
    manifest_paths = [
        ROOT / "ellmos-module.v2.json",
        ROOT / "pure-locking" / "ellmos-module.v2.json",
        ROOT / "permission-control" / "ellmos-module.v2.json",
        ROOT / "team-lock" / "ellmos-module.v2.json",
    ]
    for mpath in manifest_paths:
        assert mpath.is_file(), f"Expected manifest {mpath} to exist"
        data = json.loads(mpath.read_text(encoding="utf-8"))
        assert data.get("source_of_truth", {}).get("repository") == "https://github.com/ellmos-ai/lock-master"


def test_documentation_links_and_no_file_uris():
    """Verify that all markdown documentation files use portable links without local file:/// URIs."""
    doc_files = [
        "README.md",
        "README_de.md",
        "README_es.md",
        "README_ja.md",
        "README_ru.md",
        "README_zh-Hans.md",
        "llms.txt",
        "SECURITY.md",
        "CHANGELOG.md",
        "TODO.md",
        "LOCK-SYSTEM.md",
    ]
    for filename in doc_files:
        doc_path = ROOT / filename
        assert doc_path.is_file(), f"Expected documentation file {filename} to exist"
        content = doc_path.read_text(encoding="utf-8")
        assert "file:///" not in content, f"Found local file:/// URI in {filename}"


def test_llms_txt_integrity():
    """Verify that llms.txt contains proper discovery metadata, version, and current timestamp."""
    llms_path = ROOT / "llms.txt"
    assert llms_path.is_file()
    content = llms_path.read_text(encoding="utf-8")
    assert "Last-checked: 2026-08-16" in content
    assert "Version: 1.5.1" in content or "1.5.1" in content
    assert "120 passed" in content
    assert "ellmos-ai" in content
    assert "open-bricks" in content


def test_readme_badges_and_ecosystem_parity():
    """Verify that README.md and README_de.md include language switchers, up-to-date badges, and sibling matrices."""
    for filename in ("README.md", "README_de.md"):
        content = (ROOT / filename).read_text(encoding="utf-8")
        assert "pytest-120%20passed" in content
        assert "1.5.1" in content
        assert "ellmos--ai" in content
        assert "open--bricks" in content
        assert "llms.txt" in content
        assert "ticket-master" in content
        assert "gardener" in content
        assert "clutch" in content



def test_pyproject_tooling_integrity():
    """Verify that pyproject.toml defines build, metadata, and ruff linting configuration."""
    with (ROOT / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)

    project = pyproject.get("project", {})
    assert project.get("name") == "lock-master"
    assert project.get("requires-python") == ">=3.10"
    assert "lock-files" in project.get("keywords", [])

    tool = pyproject.get("tool", {})
    assert "ruff" in tool
    assert tool["ruff"].get("line-length") == 120
