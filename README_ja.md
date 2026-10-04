<p align="center"><img src="assets/banner.svg" alt="lock-master" width="100%"></p>

# lock-master

[EN](README.md) | [DE](README_de.md) | [ES](README_es.md) | **JA** | [RU](README_ru.md) | [ZH](README_zh-Hans.md)

**マルチエージェントプロジェクト調整のための、ポータブルかつ設定ベースのファイルロックシステム。**

lock-masterは、プレーンテキストファイルに基づく、軽量でゼロ依存のロックプロトコルを提供します。プロジェクトディレクトリ内の`LOCK*.txt`ファイルは、そのプロジェクトまたはコンポーネントが現在使用中であることを示します。有効な期限切れでないロックが存在する間は、エージェント、自動化スクリプト、またはループがそのエリアを変更してはなりません。

---

## 機能

- **スコープベースのロック:** `LOCK.txt`はプロジェクト全体をロックし、`LOCK.<scope>.txt`は特定のコンポーネントをロックします。複数のエージェントが同一プロジェクトの異なるスコープで並行して作業できます。
- **自動期限切れ:** すべてのロックには設定可能な`expires_after`期間があります（デフォルト24時間）。クリーンアップスクリプトが放置されたロックを削除します。
- **読み取り専用スキャン:** `lock_scan.py`は設定されたすべてのルートにわたるアクティブなロックを、ファイルを変更せずにリストアップします。
- **Markdownキャッシュ:** `lock_scan.py --write-cache`はスキャン不要で即時ステータス確認できる`LOCK-CACHE.md`を書き出します。
- **ドライランプルーン:** `prune_stale_locks.py --dry-run`は削除されるものをプレビューします。
- **ゼロ依存:** 純粋なPython標準ライブラリ（3.10+）のみ使用。
- **設定ベース:** すべてのルート、深度制限、スキップディレクトリ、キャッシュターゲットは`lock_roots.json`に定義されています。コード内にハードコードされたパスはありません。

---

## クイックスタート

### 1. スクリプトをコピーする

```
pure-locking/lock_utils.py
pure-locking/lock_scan.py
pure-locking/prune_stale_locks.py
pure-locking/LOCK_TEMPLATE.txt
```

任意のディレクトリ（例: `scripts/`）に配置してください。

### 2. `lock_roots.json`を作成する

`pure-locking/lock_roots.example.json`をコピーし、`lock_roots.json`にリネームして、プレースホルダーのパスを実際のプロジェクトルートに置き換えます。このファイルはローカルの絶対パスを含むため、`.gitignore`によってバージョン管理から除外されます。

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
      "name": "システム全体",
      "path": "/path/to/scripts/LOCK-CACHE.{host}.md"
    }
  ]
}
```

### 3. ロックを作成する

`pure-locking/LOCK_TEMPLATE.txt`をプロジェクトディレクトリにコピーし、フィールドを記入してから`LOCK.txt`（またはコンポーネントレベルのロックには`LOCK.<scope>.txt`）にリネームします:

```
owner: my-agent
created: 2026-06-14T10:00
expires_after: 24h
mode: hard
purpose: 認証モジュールのリファクタリング
```

### 4. アクティブなロックをリストアップする

```bash
python lock_scan.py
python lock_scan.py --json
```

### 5. 期限切れのロックを削除する

```bash
# プレビュー（安全）:
python prune_stale_locks.py --dry-run

# 実際に削除する:
python prune_stale_locks.py
```

### 6. キャッシュを更新する

```bash
python lock_scan.py --write-cache
```

`lock_roots.json`の`"caches"`キーに定義された場所に`LOCK-CACHE.md`を書き出します。

---

## ロックファイルの形式

プレーンテキスト、1行に`キー: 値`を1つ記述。`#`で始まる行はコメントです。

| フィールド          | 必須     | 例                   | 意味 |
|---------------------|----------|----------------------|------|
| `owner`             | はい     | `my-agent`           | ロックを保持しているエージェント。 |
| `created`           | はい     | `2026-06-14T10:00`   | ISOタイムスタンプ。期限切れ計算の基準となります。 |
| `expires_after`     | 任意     | `24h`, `90m`, `2d`   | 期間文字列。デフォルト: `24h`。 |
| `release_condition` | 任意     | `PRがマージされた時` | 自由記述: ロックを解除できる条件。 |
| `mode`              | 任意     | `hard` \| `soft`     | `hard` = 変更不可（デフォルト）; `soft` = 読み取り/ヒントは許可。 |
| `purpose`           | 任意     | `機能Xの追加`        | 実行中の内容を説明する自由記述。 |
| `scope`             | 任意     | `frontend`           | 情報提供のみ; **ファイル名**が正式な値です。 |

`created`が存在しないか解析できない場合、ファイルのmtimeがフォールバックとして使用されます。

---

## スコープの命名規則

| ファイル名           | 検出されるスコープ | ロック対象 |
|----------------------|--------------------|------------|
| `LOCK.txt`           | `project`          | プロジェクトディレクトリ全体 |
| `LOCK.api.txt`       | `api`              | `api`コンポーネントのみ |
| `LOCK.frontend.txt`  | `frontend`         | `frontend`コンポーネントのみ |
| `LOCK.my_scope.txt`  | `my_scope`         | 任意の名前のサブエリア |

検出正規表現: `^LOCK(\.[^.]+)?\.txt$`（大文字・小文字を区別しない）。

---

## ライフサイクル

```
確認  -->  取得  -->  解放
```

1. **確認:** プロジェクトまたはコンポーネントでの作業を開始する前に、その領域をカバーするアクティブな`LOCK*.txt`が存在しないか確認する。存在し、かつ期限切れでない場合は、別のタスクを選ぶかまたは待機する。
2. **取得:** テンプレートから自分のロックファイルを作成する（`owner`, `created`, `expires_after`, `purpose`）。
3. **解放:** 作業完了後、**自分のロックファイルを削除する**。能動的な解放が必須です; `expires_after`タイムアウトは放置されたロックのためのセーフティネットにすぎません。作業が予想より長引く場合は、`created`を更新して早期期限切れを防いでください。

---

## 設定リファレンス（`lock_roots.json`）

| キー                | 型       | デフォルト | 説明 |
|---------------------|----------|------------|------|
| `default_max_depth` | int      | `4`        | 各ルートからの最大ディレクトリ再帰深度。 |
| `shallow_depth`     | int      | `2`        | `"shallow": true`とマークされたルートの深度。 |
| `skip_dirs`         | string[] | `[]`       | 完全にスキップするディレクトリ名（サブツリーを含む）。 |
| `roots`             | object[] | `[]`       | `{ "path": "...", "shallow": true/false }`のリスト。 |
| `caches`            | object[] | `[]`       | キャッシュターゲット: `{ "name", "path", "filter_prefix?" }`。 |

**キャッシュエントリのフィールド:**

| キー            | 必須 | 説明 |
|-----------------|------|------|
| `name`          | はい | キャッシュのタイトルとして使用される表示名。 |
| `path`          | はい | `LOCK-CACHE.md`が書き込まれる絶対パス。 |
| `filter_prefix` | 任意 | このプレフィックスで始まるパスのロックのみを含める。 |

`"caches"`が省略された場合、`--write-cache`は`lock_scan.py`の隣に単一の`LOCK-CACHE.md`を書き出します。

---

## Python API

```python
from pathlib import Path
import lock_utils

project = Path("/path/to/my-project")

# 作業開始前に確認する
active = lock_utils.active_locks(project)
if active:
    print(f"ロック中: {active}")
else:
    print("作業可能です。")

# 特定のロックファイルを解析する
data = lock_utils.parse_lock_file(project / "LOCK.txt")
print(data["owner"], data["created"])

# 期限切れを確認する
from datetime import datetime
expired = lock_utils.is_expired(project / "LOCK.txt", now=datetime.now())
```

---

## テストの実行

```bash
python -m pytest tests/ -v
```

`pytest`が必要です（`pip install pytest`）。

---

## ファイル構成

```
lock-master/
├── pure-locking/
│   ├── lock_utils.py           # コアライブラリ: 解析、スコープ、期限切れ
│   ├── lock_scan.py            # CLI: アクティブなロックのリストアップ、キャッシュの書き込み
│   ├── prune_stale_locks.py    # CLI: 期限切れのロックの削除
│   ├── LOCK_TEMPLATE.txt       # 新しいロックを作成するためのテンプレート
│   └── lock_roots.example.json # 注釈付きの設定例
├── LOCK-SYSTEM.md          # 正式な仕様とライフサイクルリファレンス
├── tests/
│   └── test_smoke.py       # スモークテスト
├── LICENSE                 # MIT
├── CHANGELOG.md
├── TODO.md
├── SECURITY.md
├── llms.txt
└── VERSION
```

---

## 要件

- Python 3.10+
- サードパーティ依存なし（標準ライブラリのみ）
- テスト用: `pytest`

---

## ライセンス

MIT -- Copyright (c) 2026 Lukas Geiger. [LICENSE](LICENSE)を参照してください。
