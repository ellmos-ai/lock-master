"""Static contract checks for the watcher permissions UI."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "pure-locking" / "watcher" / "static"


def test_permissions_button_is_reachable_from_watcher_index():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'onclick="showPermissions()"' in html
    assert "Sperren/Rechte" in html


def test_permissions_ui_wires_user_and_bulk_lock_endpoints():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    for marker in (
        "function showPermissions()",
        "function createUserLock()",
        "function removeUserLock()",
        "function runBulk(action, commit)",
        "'/api/user-lock'",
        "'/api/user-lock/remove'",
        "'/api/bulk-lock'",
        "'/api/bulk-unlock'",
        "window.confirm",
        "commit",
    ):
        assert marker in app


def test_permissions_ui_explains_protected_user_locks():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "Agenten und Prune fassen ihn nicht an" in app
    assert "User-Locks werden" in app
