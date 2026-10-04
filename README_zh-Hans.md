<p align="center"><img src="assets/banner.svg" alt="lock-master" width="100%"></p>

# lock-master

[EN](README.md) | [DE](README_de.md) | [ES](README_es.md) | [JA](README_ja.md) | [RU](README_ru.md) | **ZH**

**可移植的、基于配置的文件锁定系统，用于多智能体项目协调。**

lock-master 提供了一个基于纯文本文件的轻量级、零依赖锁定协议。项目目录中的 `LOCK*.txt` 文件表示该项目或某个组件当前正在使用中——在有效且未过期的锁存在期间，任何智能体、自动化脚本或循环都不应修改该区域。

---

## 功能特性

- **基于范围的锁定（scope）：** `LOCK.txt` 锁定整个项目；`LOCK.<scope>.txt` 锁定某个组件。多个智能体可以在同一项目的不同范围上并行工作。
- **自动过期：** 每个锁都有可配置的 `expires_after` 时长（默认 24 小时）。清理脚本会删除被遗忘的锁。
- **只读扫描：** `lock_scan.py` 可在不修改任何文件的情况下列出所有配置根目录下的活跃锁。
- **Markdown 缓存：** `lock_scan.py --write-cache` 写入一个 `LOCK-CACHE.md`，无需扫描即可即时查看状态概览。
- **试运行剪枝：** `prune_stale_locks.py --dry-run` 预览将要删除的内容。
- **零依赖：** 纯 Python 标准库（3.10+）。
- **基于配置：** 所有根目录、深度限制、跳过目录和缓存目标均定义于 `lock_roots.json`——代码中没有硬编码路径。

---

## 快速上手

### 1. 复制脚本

```
pure-locking/lock_utils.py
pure-locking/lock_scan.py
pure-locking/prune_stale_locks.py
pure-locking/LOCK_TEMPLATE.txt
```

将它们放置在您选择的目录中（例如 `scripts/`）。

### 2. 创建 `lock_roots.json`

复制 `pure-locking/lock_roots.example.json`，将其重命名为 `lock_roots.json`，并将占位符路径替换为您的实际项目根目录。该文件因包含本地绝对路径而被 `.gitignore` 排除在版本控制之外。

```json
{
  "default_max_depth": 4,
  "shallow_depth": 2,
  "skip_dirs": [".git", ".venv", "node_modules", "__pycache__", "build", "dist"],
  "roots": [
    { "path": "/path/to/project-a" },
    { "path": "/path/to/project-b" },
    { "path": "/path/to/large-tree", "shallow": true }
  ],
  "caches": [
    {
      "name": "全系统",
      "path": "/path/to/scripts/LOCK-CACHE.{host}.md"
    }
  ]
}
```

### 3. 创建锁

将 `pure-locking/LOCK_TEMPLATE.txt` 复制到您的项目目录，填写各字段，然后将其重命名为 `LOCK.txt`（或用于组件级锁定的 `LOCK.<scope>.txt`）：

```
owner: my-agent
created: 2026-06-14T10:00
expires_after: 24h
mode: hard
purpose: 重构认证模块
```

### 4. 列出活跃的锁

```bash
python lock_scan.py
python lock_scan.py --json
```

### 5. 删除已过期的锁

```bash
# 预览（安全）：
python prune_stale_locks.py --dry-run

# 实际删除：
python prune_stale_locks.py
```

### 6. 刷新缓存

```bash
python lock_scan.py --write-cache
```

按照 `lock_roots.json` 中 `"caches"` 键的定义写入 `LOCK-CACHE.md`。

---

## 锁文件格式

纯文本，每行一个 `键: 值`。以 `#` 开头的行为注释。

| 字段                | 必填     | 示例                  | 含义 |
|---------------------|----------|-----------------------|------|
| `owner`             | 是       | `my-agent`            | 持有锁的所有者。 |
| `created`           | 是       | `2026-06-14T10:00`    | ISO 时间戳；过期计算的基准。 |
| `expires_after`     | 可选     | `24h`, `90m`, `2d`    | 时长字符串。默认值：`24h`。 |
| `release_condition` | 可选     | `PR 已合并`           | 自由文本：何时可以释放锁。 |
| `mode`              | 可选     | `hard` \| `soft`      | `hard` = 不允许修改（默认）；`soft` = 允许读取/提示。 |
| `purpose`           | 可选     | `添加功能 X`          | 对运行内容的自由文本描述。 |
| `scope`             | 可选     | `frontend`            | 仅供参考；**文件名**才是权威来源。 |

如果 `created` 缺失或无法解析，则使用文件的 mtime 作为备用值。

---

## 范围（scope）命名约定

| 文件名               | 检测到的范围 | 锁定的内容 |
|----------------------|--------------|------------|
| `LOCK.txt`           | `project`    | 整个项目目录 |
| `LOCK.api.txt`       | `api`        | 仅 `api` 组件 |
| `LOCK.frontend.txt`  | `frontend`   | 仅 `frontend` 组件 |
| `LOCK.my_scope.txt`  | `my_scope`   | 任意自由命名的子区域 |

检测正则表达式：`^LOCK(\.[^.]+)?\.txt$`（不区分大小写）。

---

## 生命周期

```
遵守  -->  占用  -->  释放
```

1. **遵守：** 在开始处理某个项目或组件之前，检查是否存在覆盖该区域的活跃 `LOCK*.txt`。若存在且未过期，则选择其他任务或等待。
2. **占用：** 从模板创建您自己的锁文件（`owner`, `created`, `expires_after`, `purpose`）。
3. **释放：** 完成后**删除自己创建的锁文件**。主动释放是必须的；`expires_after` 超时仅是针对被遗忘锁的安全保障。如果工作耗时超过预期，请更新 `created` 以防止提前过期。

---

## 配置参考（`lock_roots.json`）

| 键                  | 类型     | 默认值 | 描述 |
|---------------------|----------|--------|------|
| `default_max_depth` | int      | `4`    | 从每个根目录开始的最大目录递归深度。 |
| `shallow_depth`     | int      | `2`    | 标记了 `"shallow": true` 的根目录的深度。 |
| `skip_dirs`         | string[] | `[]`   | 要完全跳过的目录名（包括子目录树）。 |
| `roots`             | object[] | `[]`   | `{ "path": "...", "shallow": true/false }` 的列表。 |
| `caches`            | object[] | `[]`   | 缓存目标：`{ "name", "path", "filter_prefix?" }`。 |

**缓存条目字段：**

| 键              | 必填 | 描述 |
|-----------------|------|------|
| `name`          | 是   | 用作缓存标题的显示名称。 |
| `path`          | 是   | 写入 `LOCK-CACHE.md` 的绝对路径。 |
| `filter_prefix` | 可选 | 仅包含路径以此前缀开头的锁。 |

如果省略 `"caches"`，`--write-cache` 会在 `lock_scan.py` 旁边写入单个 `LOCK-CACHE.md`。

---

## Python API

```python
from pathlib import Path
import lock_utils

project = Path("/path/to/my-project")

# 开始工作前检查
active = lock_utils.active_locks(project)
if active:
    print(f"已锁定：{active}")
else:
    print("可以开始工作。")

# 解析特定的锁文件
data = lock_utils.parse_lock_file(project / "LOCK.txt")
print(data["owner"], data["created"])

# 检查是否过期
from datetime import datetime
expired = lock_utils.is_expired(project / "LOCK.txt", now=datetime.now())
```

---

## 运行测试

```bash
python -m pytest tests/ -v
```

需要安装 `pytest`（`pip install pytest`）。

---

## 文件结构

```
lock-master/
├── pure-locking/
│   ├── lock_utils.py           # 核心库：解析、范围、过期处理
│   ├── lock_scan.py            # CLI：列出活跃锁，写入缓存
│   ├── prune_stale_locks.py    # CLI：删除过期锁
│   ├── LOCK_TEMPLATE.txt       # 创建新锁的模板
│   └── lock_roots.example.json # 带注释的示例配置
├── LOCK-SYSTEM.md          # 规范说明与生命周期参考
├── tests/
│   └── test_smoke.py       # 冒烟测试
├── LICENSE                 # MIT
├── CHANGELOG.md
├── TODO.md
├── SECURITY.md
├── llms.txt
└── VERSION
```

---

## 环境要求

- Python 3.10+
- 无第三方依赖（仅使用标准库）
- 运行测试需要：`pytest`

---

## 许可证

MIT -- Copyright (c) 2026 Lukas Geiger。参见 [LICENSE](LICENSE)。
