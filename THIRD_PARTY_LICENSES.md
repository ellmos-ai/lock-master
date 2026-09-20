# Third-Party Licenses & Transparency Notice

> **Project:** `ellmos-ai/lock-master`  
> **Audited:** 2026-09-20  
> **Repository License:** [MIT License](LICENSE)  
> **Architecture & Privacy:** 100% Local-First, Zero-Egress, Unprivileged User-Mode (`RunAsInvoker`), Fail-Closed

---

## Executive Summary & Compliance Assurance

`lock-master` is engineered under strict architectural and governance invariants: **100% Local-First, Zero-Egress by default, unprivileged user-mode execution (`RunAsInvoker`), and fail-closed lock semantics**. All file-based mutex operations, scoped locks (`LOCK.<scope>.txt`), multi-agent team locks (`LOCK.team.<host>.txt`), rule evaluations (`LOCK.permissions.json`), and background watcher routines operate entirely within local process and filesystem boundaries.

All direct, runtime, and development dependencies utilized across `lock-master` are distributed under strictly **permissive and free open-source licenses** (MIT, Apache-2.0, PSFL, BSD-3-Clause). There are **zero AGPL or restrictive copyleft constraints**, ensuring maximum portability for multi-machine setups, personal transfer yards, enterprise infrastructure, and automated multi-agent deployments.

Furthermore, `lock-master` guarantees:
1. **100% Local-First & Zero Egress (INV-LOCAL-01):** Operates entirely on local filesystems and local storage mounts (OneDrive, Dropbox, Syncthing, NAS, or local paths). Zero network telemetry, zero phone-home calls, and zero hidden analytical tracking.
2. **Unprivileged User-Mode (`RunAsInvoker` / INV-SEC-02):** Executes safely in unprivileged user space without requiring root or administrator elevation.
3. **Fail-Closed Lock Semantics (INV-FAIL-03):** When a lock check encounters ambiguous lock states, syntax errors, or active non-expired locks, access is strictly denied (read-only safe default).
4. **Hierarchical & Scoped Locking (INV-SCOPE-04):** Root `LOCK.txt` protects an entire repository; granular `LOCK.<scope>.txt` allows parallel agent concurrency across isolated project components.
5. **Multi-Agent Team Coordination (INV-TEAM-05):** `LOCK.team.<host>.txt` coordinates intra-host agent teams (presence, file claims, tool claims, and message boards) while signaling peer hosts to stand by during cloud-sync latency.
6. **Deterministic TTL & Safe Stale Pruning (INV-TTL-06):** Every lock defines an explicit `expires_after` duration (default 24h); stale cleanup (`prune_stale_locks.py`) provides safe `--dry-run` inspection before atomic unlink.
7. **Declarative Permission Engine (INV-PERM-07):** Evaluates access actions against `LOCK.permissions.json` using strict priority ordering (`deny` > `ask` > `allow` > default) and regex path matching.
8. **Read-Only Inspection & Atomic Cache (INV-AUDIT-08):** `lock_scan.py` inspects workspace hierarchies in pure read-only mode; cache exports (`LOCK-CACHE.md`) are written atomically without side effects on active locks.
9. **100% Permissive Audited Dependency Stack (INV-LIC-09):** Zero external runtime dependencies (pure Python standard library); development and testing tools are strictly MIT/PSFL/BSD-3-Clause audited.
10. **Dual Security Response & Triage SLA (INV-SLA-10):** Commitments to 48-hour response, 5-business-day triage, and 30-day remediation via canonical security channels (`security@open-bricks.org`, `security@ellmos.ai`).

---

## Level 1 Software Bill of Materials (SBOM)

| Component / Artifact | Type | Declared License | Upstream Source | Copyleft / AGPL | Role & Scope |
|:---|:---|:---|:---|:---|:---|
| **Python Standard Library** | Runtime Core | [PSFL-2.0](https://docs.python.org/3/license.html) | [python/cpython](https://github.com/python/cpython) | **None** (100% Permissive) | File I/O, process, hashlib, json, logging, path manipulation |
| **tomli** (Python < 3.11) | Runtime Fallback | [MIT](https://github.com/hukkin/tomli/blob/master/LICENSE) | [hukkin/tomli](https://github.com/hukkin/tomli) | **None** (100% Permissive) | Python 3.10 TOML parsing compatibility fallback |
| **pytest** | Development / Test | [MIT](https://github.com/pytest-dev/pytest/blob/main/LICENSE) | [pytest-dev/pytest](https://github.com/pytest-dev/pytest) | **None** (100% Permissive) | Test execution and contract verification runner |
| **ruff** | Development / QA | [MIT / Apache-2.0](https://github.com/astral-sh/ruff/blob/main/LICENSE-MIT) | [astral-sh/ruff](https://github.com/astral-sh/ruff) | **None** (100% Permissive) | Static linting and code style gate |
| **setuptools** | Build Backend | [MIT](https://github.com/pypa/setuptools/blob/main/LICENSE) | [pypa/setuptools](https://github.com/pypa/setuptools) | **None** (100% Permissive) | PEP 517 / PEP 621 packaging build backend |
| **flask** | Optional Watcher UI | [BSD-3-Clause](https://github.com/pallets/flask/blob/main/LICENSE.txt) | [pallets/flask](https://github.com/pallets/flask) | **None** (100% Permissive) | Optional localhost daemon & browser UI runner |
| **anyio** | Development / Test | [MIT](https://github.com/agronholm/anyio/blob/master/LICENSE) | [agronholm/anyio](https://github.com/agronholm/anyio) | **None** (100% Permissive) | Concurrency testing abstraction fixture |

### Zero-Runtime-Dependency & Zero-Copyleft Isolation Guarantee

The core runtime of `lock-master` executes with **zero external dependencies** (`dependencies = []` in `pyproject.toml`) and guarantees:
- **Zero-Runtime-Dependency Guarantee**: Pure Python standard library (3.10+) provides 100% of all locking, scanning, parsing, pruning, and verification logic.
- **Zero-Copyleft Isolation Guarantee**: 0% GPL, 0% AGPL, and 0% reciprocal licenses at runtime or in build paths. All dependencies are MIT, Apache-2.0, PSFL-2.0, or BSD-3-Clause.
- **Unprivileged User-Mode Execution (`RunAsInvoker`)**: Never attempts administrative privilege escalation, UAC prompts, or root filesystem writes.

---

## Invariant Cross-Reference Matrix

| Invariant ID | Name & Semantic Contract | Verification Mechanism & Implementation Component |
|:---|:---|:---|
| **INV-LOCAL-01** | 100% Local-First & Zero Egress | `pure-locking/lock_utils.py`, `tests/test_metadata.py::test_ast_zero_hardcoded_secrets_and_personal_paths` |
| **INV-SEC-02** | Unprivileged User-Mode (`RunAsInvoker`) | `team-lock/team_lock.py`, `pure-locking/lock_create.py` (Local user space only) |
| **INV-FAIL-03** | Fail-Closed Default Semantics | `pure-locking/lock_utils.py` (`active_locks()`, read-only error defaults) |
| **INV-SCOPE-04** | Scoped & Component Granularity | `pure-locking/lock_utils.py` (`parse_lock_file()`, `LOCK.<scope>.txt`) |
| **INV-TEAM-05** | Intra-Host Multi-Agent Teams | `team-lock/_lock_master_team/api.py`, `LOCK.team.<host>.txt` sub-claims |
| **INV-TTL-06** | Deterministic TTL & Safe Stale Pruning | `pure-locking/prune_stale_locks.py` (`--dry-run` and explicit `expires_after`) |
| **INV-PERM-07** | Declarative Permission Engine | `permission-control/permissions.py` (`LOCK.permissions.json` deny > ask > allow) |
| **INV-AUDIT-08** | Read-Only Inspection & Atomic Cache | `pure-locking/lock_scan.py` (`--write-cache` to `LOCK-CACHE.md`) |
| **INV-LIC-09** | Permissive Audited Dependency Stack | `THIRD_PARTY_LICENSES.md`, `tests/test_metadata.py::test_supply_chain_zero_external_runtime_imports` |
| **INV-SLA-10** | Dual Security Response & Triage SLA | `SECURITY.md`, `tests/test_metadata.py::test_security_slas_and_30d_remediation` |

---

## Full License Texts (Excerpts & Notices)

### 1. Python Software Foundation License Version 2 (PSFL-2.0)
Python standard library modules are used under the PSF License Agreement.  
Copyright (c) 2001-2026 Python Software Foundation. All rights reserved.

### 2. MIT License (MIT)
Used by `lock-master`, `tomli`, `pytest`, `setuptools`, and `anyio`.

> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:  
>  
> The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.  
>  
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

### 3. BSD-3-Clause License (BSD-3-Clause)
Used by `flask`.

> Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:  
> 1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.  
> 2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.  
> 3. Neither the name of the copyright holder nor the names of its contributors may be used to endorse or promote products derived from this software without specific prior written permission.

### 4. Apache License Version 2.0 (Apache-2.0)
Dual-license option used by `ruff`.
Licensed under the Apache License, Version 2.0. You may obtain a copy of the License at `http://www.apache.org/licenses/LICENSE-2.0`.
