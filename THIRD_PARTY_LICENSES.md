# Third-Party Licenses & Transparency Notice

> **Project:** `ellmos-ai/lock-master`  
> **Audited:** 2026-09-11  
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
10. **Dual Security Response & Triage SLA (INV-SLA-10):** Commitments to 48-hour response and 5-business-day triage via canonical security channels (`security@open-bricks.org`, `security@ellmos.ai`).

---

## Runtime Dependency Matrix

| Package | Role / Functional Scope | License | Project Repository / Upstream |
|:---|:---|:---|:---|
| **Python Standard Library** | Core CLI runner, file arithmetic, process execution, hashlib, json, logging, path manipulation | [PSFL-2.0](https://docs.python.org/3/license.html) | [python/cpython](https://github.com/python/cpython) |
| **tomli** (Python < 3.11) | Standard TOML parsing fallback for Python 3.10 runtime environments | [MIT](https://github.com/hukkin/tomli/blob/master/LICENSE) | [hukkin/tomli](https://github.com/hukkin/tomli) |

---

## Development & Quality Assurance Tooling

| Package | Usage & Purpose | License | Source / Upstream |
|:---|:---|:---|:---|
| **pytest** | Automated test runner, contract verification suites, mock fixtures | [MIT](https://github.com/pytest-dev/pytest/blob/main/LICENSE) | [pytest-dev/pytest](https://github.com/pytest-dev/pytest) |
| **ruff** | High-performance Python linter and code formatting enforcement | [MIT / Apache-2.0](https://github.com/astral-sh/ruff/blob/main/LICENSE-MIT) | [astral-sh/ruff](https://github.com/astral-sh/ruff) |
| **setuptools** | Standard package build backend (PEP 517 / PEP 621 compliant) | [MIT](https://github.com/pypa/setuptools/blob/main/LICENSE) | [pypa/setuptools](https://github.com/pypa/setuptools) |
| **flask** | Optional lightweight localhost web UI daemon runner in `pure-locking/watcher/` | [BSD-3-Clause](https://github.com/pallets/flask/blob/main/LICENSE.txt) | [pallets/flask](https://github.com/pallets/flask) |
| **anyio** | Optional asynchronous networking concurrency abstraction for test harnesses | [MIT](https://github.com/agronholm/anyio/blob/master/LICENSE) | [agronholm/anyio](https://github.com/agronholm/anyio) |

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
