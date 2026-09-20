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
    assert version_file == "1.6.3"

    with (ROOT / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)
    assert pyproject["project"]["version"] == "1.6.3"

    with (ROOT / "ellmos-module.v2.json").open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    assert manifest["version"] == "1.6.3"


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
    """Verify that llms.txt contains proper discovery metadata, version, invariants, and current timestamp."""
    llms_path = ROOT / "llms.txt"
    assert llms_path.is_file()
    content = llms_path.read_text(encoding="utf-8")
    assert "Last-checked: 2026-09-20" in content or "Last-checked: 2026-09-16" in content
    assert "Version: 1.6.3" in content or "1.6.3" in content
    assert "isolated wheel install/import/CLI smoke" in content
    assert "ellmos-ai" in content
    assert "open-bricks" in content
    assert "THIRD_PARTY_LICENSES.md" in content
    assert "MARKETING-LOG.txt" in content
    assert "INV-LOCAL-01" in content


def test_readme_badges_and_ecosystem_parity():
    """Verify that README.md and README_de.md include language switchers, up-to-date badges, and sibling matrices."""
    for filename in ("README.md", "README_de.md"):
        content = (ROOT / filename).read_text(encoding="utf-8")
        assert (
            "tests-239%20passed" in content
            or "tests-233%20passed" in content
            or "tests-228%20passed" in content
            or "tests-212%20passed" in content
            or "pytest-passing" in content
        )
        assert "1.6.3" in content
        assert "ellmos--ai" in content
        assert "open--bricks" in content
        assert "llms.txt" in content
        assert "THIRD_PARTY_LICENSES.md" in content
        assert "MARKETING-LOG.txt" in content
        assert "ticket-master" in content
        assert "gardener" in content
        assert "clutch" in content
        assert "system-gap-master" in content
        assert "system-explorer" in content
        assert "open-compute-mcp" in content
        assert "ellmos-filecommander-mcp" in content
        assert "ellmos-codecommander-mcp" in content
        assert "n8n-manager-mcp" in content


def test_pyproject_tooling_integrity():
    """Verify that pyproject.toml defines build, metadata, classifiers, and ruff linting configuration."""
    with (ROOT / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)

    project = pyproject.get("project", {})
    assert project.get("name") == "lock-master"
    assert project.get("requires-python") == ">=3.10"
    assert "lock-files" in project.get("keywords", [])
    assert "zero-egress" in project.get("keywords", [])
    assert "ellmos-ai" in project.get("keywords", [])
    assert "open-bricks" in project.get("keywords", [])
    assert project.get("license-files") == ["LICENSE", "NOTICE", "THIRD_PARTY_LICENSES.md"]

    classifiers = project.get("classifiers", [])
    assert "Programming Language :: Python :: 3.10" in classifiers
    assert "Programming Language :: Python :: 3.11" in classifiers
    assert "Programming Language :: Python :: 3.12" in classifiers
    assert "Programming Language :: Python :: 3.13" in classifiers
    assert "Operating System :: OS Independent" in classifiers

    urls = project.get("urls", {})
    assert "Homepage" in urls
    assert "Documentation" in urls
    assert "Repository" in urls
    assert "Bug Tracker" in urls
    assert "Changelog" in urls
    assert "Security" in urls
    assert "Third-Party Licenses" in urls
    assert "Marketing Log" in urls
    assert "LLM Ready" in urls
    assert urls.get("Parent Organization") == "https://github.com/ellmos-ai"
    assert urls.get("Umbrella Ecosystem") == "https://github.com/open-bricks"

    tool = pyproject.get("tool", {})
    assert "ruff" in tool
    assert tool["ruff"].get("line-length") == 120


def test_pyproject_pytest_configuration():
    """Verify that pyproject.toml defines standardized pytest configuration."""
    with (ROOT / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)
    pytest_cfg = pyproject.get("tool", {}).get("pytest", {}).get("ini_options", {})
    assert pytest_cfg.get("testpaths") == ["tests"]
    assert pytest_cfg.get("pythonpath") == ["."]
    assert "-ra" in pytest_cfg.get("addopts", "")


def test_bilingual_readme_navigation_parity():
    """Verify that README.md and README_de.md have full 18-point quick navigation parity and heading targets."""
    en_content = (ROOT / "README.md").read_text(encoding="utf-8")
    de_content = (ROOT / "README_de.md").read_text(encoding="utf-8")

    en_anchors = [
        "#executive-summary--core-identity",
        "#visual-architecture-topology",
        "#end-to-end-multi-agent-locking-lifecycle",
        "#marketing--target-personas",
        "#comparative-matrix-vs-alternatives",
        "#governance--runtime-invariants",
        "#core-capabilities--lock-types",
        "#start-here",
        "#quick-start",
        "#configuration",
        "#optional-watcher-ui",
        "#python-api",
        "#file-layout--shims",
        "#running-tests",
        "#security-policy",
        "#third-party-licenses--transparency",
        "#ecosystem--sibling-tools",
        "#license",
    ]

    de_anchors = [
        "#management-zusammenfassung--kernidentitaet",
        "#visuelle-architektur-topologie",
        "#end-to-end-multi-agenten-sperr-lebenszyklus",
        "#marketing--zielgruppen",
        "#vergleichsmatrix-gegenueber-alternativen",
        "#governance--laufzeit-invarianten",
        "#kernfaehigkeiten--sperrtypen",
        "#einstieg",
        "#schnellstart",
        "#konfiguration",
        "#optionales-watcher-web-ui",
        "#python-api",
        "#dateistruktur--shims",
        "#tests-ausführen",
        "#sicherheitsrichtlinie",
        "#drittanbieter-lizenzen--transparenz",
        "#ökosystem--geschwisterwerkzeuge",
        "#lizenz",
    ]

    assert len(en_anchors) == 18
    assert len(de_anchors) == 18

    for anchor in en_anchors:
        assert f"({anchor})" in en_content, f"Anchor {anchor} missing in README.md Quick Navigation"
        anchor_id = anchor.lstrip("#")
        assert f'id="{anchor_id}"' in en_content, f'Target id="{anchor_id}" missing in README.md headings'

    for anchor in de_anchors:
        assert f"({anchor})" in de_content, f"Anchor {anchor} missing in README_de.md Schnellnavigation"
        anchor_id = anchor.lstrip("#")
        assert f'id="{anchor_id}"' in de_content, f'Target id="{anchor_id}" missing in README_de.md headings'


def test_governance_invariants_parity():
    """Verify that 10 canonical runtime invariants INV-LOCAL-01 to INV-SLA-10 are codified."""
    invariants = [
        "INV-LOCAL-01",
        "INV-SEC-02",
        "INV-FAIL-03",
        "INV-SCOPE-04",
        "INV-TEAM-05",
        "INV-TTL-06",
        "INV-PERM-07",
        "INV-AUDIT-08",
        "INV-LIC-09",
        "INV-SLA-10",
    ]
    for target in ("README.md", "README_de.md", "THIRD_PARTY_LICENSES.md", "llms.txt"):
        content = (ROOT / target).read_text(encoding="utf-8")
        for inv in invariants:
            assert inv in content, f"Invariant {inv} missing in {target}"


def test_third_party_licenses_metadata():
    """Verify that THIRD_PARTY_LICENSES.md exists, has proper headers, and 100% permissive licenses."""
    lic_file = ROOT / "THIRD_PARTY_LICENSES.md"
    assert lic_file.is_file(), "THIRD_PARTY_LICENSES.md must exist"
    content = lic_file.read_text(encoding="utf-8")
    assert "file:///" not in content
    assert "100% Local-First" in content
    assert "Zero-Egress" in content
    assert "RunAsInvoker" in content
    assert "PSFL-2.0" in content
    assert "MIT License" in content
    assert "pytest" in content
    assert "ruff" in content
    assert "security@open-bricks.org" in content


def test_marketing_log_present():
    """Verify that MARKETING-LOG.txt exists, contains target personas, search terms, and competitive matrix."""
    log_file = ROOT / "MARKETING-LOG.txt"
    assert log_file.is_file(), "MARKETING-LOG.txt must exist"
    content = log_file.read_text(encoding="utf-8")
    assert "TARGET PERSONAS" in content
    assert "HIGH-INTENT SEARCH QUERIES" in content
    assert "COMPETITIVE DIFFERENTIATION MATRIX" in content
    assert "STRATEGIC ACTION ITEMS" in content
    assert "Autonomous AI Agent Architects" in content
    assert "Multi-Device & Cloud-Sync Developers" in content


def test_ci_workflow_parity():
    """Verify that GitHub Actions CI workflow configures multi-OS, Python 3.10-3.13, and ruff linting."""
    ci_file = ROOT / ".github" / "workflows" / "tests.yml"
    assert ci_file.is_file(), "tests.yml must exist"
    content = ci_file.read_text(encoding="utf-8")
    assert "actions/checkout@v4" in content
    assert "actions/setup-python@v5" in content
    assert "ubuntu-latest" in content
    assert "windows-latest" in content
    assert "macos-latest" in content
    assert "3.10" in content
    assert "3.13" in content
    assert "timeout-minutes: 15" in content
    assert "ruff check ." in content
    assert "concurrency:" in content
    assert "cancel-in-progress: true" in content
    assert "python -m compileall -q ." in content


def test_ci_stale_workflow_present():
    """Verify that GitHub Actions stale workflow is present with timeout and exempt labels."""
    stale_file = ROOT / ".github" / "workflows" / "stale.yml"
    assert stale_file.is_file(), "stale.yml must exist"
    content = stale_file.read_text(encoding="utf-8")
    assert "actions/stale@v9" in content
    assert "timeout-minutes: 10" in content
    assert "days-before-stale: 30" in content
    assert "days-before-close: 7" in content
    assert "exempt-issue-labels:" in content
    assert "exempt-pr-labels:" in content


def test_security_policy_bilingual_parity():
    """Verify that SECURITY.md provides bilingual English and German policies with official contacts and SLAs."""
    sec_file = ROOT / "SECURITY.md"
    assert sec_file.is_file(), "SECURITY.md must exist"
    content = sec_file.read_text(encoding="utf-8")
    assert "## English" in content
    assert "## Deutsch" in content
    assert "security@open-bricks.org" in content
    assert "security@ellmos.ai" in content
    assert "support@lukasgeiger.com" in content
    assert "lukas@open-bricks.org" in content
    assert "1.6.x" in content
    assert "48 hours" in content or "48 Stunden" in content
    assert "5 business days" in content or "5 Werktagen" in content
    assert "Local-First" in content or "local-first" in content.lower()
    assert "Zero-Egress" in content or "zero-egress" in content.lower()
    assert "https://github.com/ellmos-ai/lock-master/security/advisories" in content


def test_gitignore_hygiene():
    """Verify that .gitignore contains conflict copy, packaging smoke, and multi-host sync defense patterns."""
    gi_path = ROOT / ".gitignore"
    assert gi_path.is_file()
    content = gi_path.read_text(encoding="utf-8")
    for pattern in [
        "*.sync-conflict-*",
        "*-CONFLIT-*",
        "* (kopie)*",
        "*conflicted copy*",
        "*-WORKSTATION*",
        "*-ASUS-GEI*",
        "wheelhouse/",
        ".wheel-smoke/",
        "*.tmp",
        "*.bak",
    ]:
        assert pattern in content, f"Pattern {pattern} missing from .gitignore"


def test_changelog_recent_pfad_a_entry():
    """Verify that CHANGELOG.md contains the 2026-09-16 Pfad A technical hygiene and CI hardening entry."""
    cl_path = ROOT / "CHANGELOG.md"
    assert cl_path.is_file(), "CHANGELOG.md must exist"
    content = cl_path.read_text(encoding="utf-8")
    assert "Pfad A Hygiene & CI Hardening - 2026-09-16" in content
    assert "CI Workflow Execution Guardrails" in content
    assert "timeout-minutes: 15" in content


def test_pep639_license_files_and_zero_runtime_dependencies():
    """Verify PEP 639 license-files, explicit zero runtime dependencies, and dev tooling."""
    with (ROOT / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)
    project = pyproject.get("project", {})

    assert project.get("license-files") == ["LICENSE", "NOTICE", "THIRD_PARTY_LICENSES.md"]
    assert project.get("dependencies") == []
    dev_deps = project.get("optional-dependencies", {}).get("dev", [])
    assert any("pytest" in d for d in dev_deps)
    assert any("ruff" in d for d in dev_deps)


def test_gitignore_security_credentials_and_sync_hygiene():
    """Verify that .gitignore excludes private keys, certs, tokens, credentials, and sync conflicts."""
    gi_path = ROOT / ".gitignore"
    assert gi_path.is_file()
    content = gi_path.read_text(encoding="utf-8")
    required_patterns = [
        "*.pem",
        "*.key",
        "*.pfx",
        "*.p12",
        "*.crt",
        "*.cert",
        "*.csr",
        "*.token",
        "*.secret",
        "credentials.json",
        "secrets.json",
        ".npmrc",
        ".pypirc",
        "id_rsa*",
        "id_ed25519*",
        "*.orig",
        "*.rej",
        "*.sync-conflict-*",
        "*-WORKSTATION-LG*",
        "*-ASUS-GEI*",
    ]
    for pattern in required_patterns:
        assert pattern in content, f"Pattern {pattern} missing from .gitignore"


def test_security_slas_and_30d_remediation():
    """Verify that SECURITY.md defines 48h initial response, 5-day triage, and 30-day remediation SLAs."""
    sec_path = ROOT / "SECURITY.md"
    assert sec_path.is_file()
    content = sec_path.read_text(encoding="utf-8")
    assert "30 calendar days" in content
    assert "30 Kalendertagen" in content
    assert "48 hours" in content
    assert "48 Stunden" in content
    assert "5 business days" in content
    assert "5 Werktagen" in content


def test_ast_zero_hardcoded_secrets_and_personal_paths():
    """Verify AST / regex scan finds zero hardcoded API keys, tokens, or developer personal paths."""
    import ast
    import re

    patterns = [
        (re.compile(r"(?i)api[_-]?key\s*[:=]\s*['\"][^'\"]{10,}['\"]"), "API Key"),
        (re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"), "Private Key"),
        (re.compile(r"C:\\Users\\lukas", re.IGNORECASE), "Personal path"),
        (re.compile(r"ghp_[a-zA-Z0-9]{36}"), "GitHub Token"),
    ]

    for py_file in ROOT.rglob("*.py"):
        if any(part in py_file.parts for part in [".git", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist", "tests"]):
            continue
        text = py_file.read_text(encoding="utf-8", errors="ignore")
        ast.parse(text, filename=str(py_file))
        for regex, desc in patterns:
            match = regex.search(text)
            assert not match, f"Found {desc} in {py_file}: {match.group(0) if match else ''}"


def test_supply_chain_zero_external_runtime_imports():
    """Verify runtime source code only imports Python standard library and internal modules."""
    import ast
    import sys

    stdlib_top_levels = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else {
        "http", "urllib", "json", "pathlib", "subprocess", "argparse", "dataclasses",
        "re", "shutil", "typing", "os", "sys", "datetime", "uuid", "hashlib", "io",
        "fcntl", "msvcrt", "contextlib", "signal", "time", "threading", "tempfile",
        "ctypes", "sqlite3", "platform", "inspect", "importlib", "socket", "stat",
        "fnmatch", "concurrent"
    }

    internal_modules = {
        "_lock_master_team", "bulk_lock", "lock_create", "lock_scan",
        "lock_status", "lock_utils", "permissions", "prune_stale_locks", "team_lock",
        "storage", "lock_watcher", "rooms", "dir_stats", "config", "scanner",
        "cache_writer", "cli", "web_server", "contested"
    }

    runtime_files = [
        ROOT / "bulk_lock.py",
        ROOT / "lock_create.py",
        ROOT / "lock_scan.py",
        ROOT / "lock_status.py",
        ROOT / "lock_utils.py",
        ROOT / "permissions.py",
        ROOT / "prune_stale_locks.py",
        ROOT / "team_lock.py",
    ]

    for sub in ["team-lock", "pure-locking", "permission-control"]:
        subdir = ROOT / sub
        if subdir.is_dir():
            runtime_files.extend([p for p in subdir.rglob("*.py") if "__pycache__" not in p.parts])

    for py_file in runtime_files:
        if not py_file.is_file():
            continue
        text = py_file.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_pkg = alias.name.split(".")[0]
                    assert (
                        root_pkg in stdlib_top_levels or root_pkg in internal_modules
                    ), f"Disallowed runtime import '{alias.name}' in {py_file}"
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue
                if node.module:
                    root_pkg = node.module.split(".")[0]
                    assert (
                        root_pkg in stdlib_top_levels or root_pkg in internal_modules
                    ), f"Disallowed runtime import from '{node.module}' in {py_file}"


def test_target_personas_sections():
    """Verify target personas PERSONA-01 through PERSONA-04 are documented across READMEs and marketing log."""
    en_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    de_readme = (ROOT / "README_de.md").read_text(encoding="utf-8")
    mkt_log = (ROOT / "MARKETING-LOG.txt").read_text(encoding="utf-8")

    personas = ["[PERSONA-01]", "[PERSONA-02]", "[PERSONA-03]", "[PERSONA-04]"]
    for p in personas:
        assert p in en_readme, f"{p} missing in README.md"
        assert p in de_readme, f"{p} missing in README_de.md"
        assert p in mkt_log, f"{p} missing in MARKETING-LOG.txt"


def test_comparative_matrix_sections():
    """Verify 10-dimension comparative matrix vs 4 alternatives exists in both READMEs."""
    en_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    de_readme = (ROOT / "README_de.md").read_text(encoding="utf-8")

    for doc in [en_readme, de_readme]:
        assert "INV-LOCAL-01" in doc
        assert "INV-SEC-02" in doc
        assert "INV-FAIL-03" in doc
        assert "INV-SCOPE-04" in doc
        assert "INV-TEAM-05" in doc
        assert "INV-TTL-06" in doc
        assert "INV-PERM-07" in doc
        assert "INV-AUDIT-08" in doc
        assert "INV-LIC-09" in doc
        assert "INV-SLA-10" in doc
        assert "Redlock" in doc


def test_notice_attribution_file():
    """Verify formal NOTICE file exists and attributes authors and ecosystems."""
    notice_path = ROOT / "NOTICE"
    assert notice_path.is_file(), "NOTICE file missing in repository root"
    content = notice_path.read_text(encoding="utf-8")
    assert "Lukas Geiger" in content
    assert "ellmos-ai" in content
    assert "open-bricks" in content
    assert "MIT" in content


def test_statutory_disclaimer_521_bgb():
    """Verify German statutory disclaimer (§ 521 BGB Gefälligkeitsrecht) is present in both READMEs."""
    en_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    de_readme = (ROOT / "README_de.md").read_text(encoding="utf-8")

    for doc in [en_readme, de_readme]:
        assert "§ 521 BGB" in doc
        assert "Gefälligkeit" in doc
        assert "Vorsatz und grobe Fahrlässigkeit" in doc


def test_level1_sbom_and_cross_reference_matrix():
    """Verify Level 1 SBOM, Invariant Cross-Reference Matrix and RunAsInvoker are in THIRD_PARTY_LICENSES.md."""
    sbom_doc = (ROOT / "THIRD_PARTY_LICENSES.md").read_text(encoding="utf-8")
    assert "Level 1 Software Bill of Materials (SBOM)" in sbom_doc
    assert "Invariant Cross-Reference Matrix" in sbom_doc
    assert "RunAsInvoker" in sbom_doc
    assert "INV-LOCAL-01" in sbom_doc
    assert "INV-SLA-10" in sbom_doc
    assert "2026-09-20" in sbom_doc


def test_dual_mermaid_diagrams_and_semicolons_free():
    """Verify dual Mermaid diagrams (flowchart TD and sequenceDiagram with autonumber) have zero trailing semicolons."""
    import re

    for readme_name in ["README.md", "README_de.md"]:
        content = (ROOT / readme_name).read_text(encoding="utf-8")
        assert "flowchart TD" in content, f"flowchart TD missing in {readme_name}"
        assert "sequenceDiagram" in content, f"sequenceDiagram missing in {readme_name}"
        assert "autonumber" in content, f"autonumber missing in {readme_name}"

        # Find all mermaid blocks and check no lines end with semicolon
        blocks = re.findall(r"```mermaid\n(.*?)\n```", content, re.DOTALL)
        assert len(blocks) >= 2, f"Expected at least 2 mermaid blocks in {readme_name}"
        for block in blocks:
            for line in block.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("%%"):
                    assert not stripped.endswith(";"), f"Trailing semicolon found in {readme_name} mermaid line: {stripped}"



