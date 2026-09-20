#!/usr/bin/env python3
"""PreToolUse guard (Bash): blockiert `git push` bei aktivem LOCK.user.*- oder LOCK.until.*-Lock im Ziel-Repo.

Hintergrund: D-20260902-002 (Zwei-Modell-Merge-Regel). Die fruehere Vier-Augen-Regel sollte die
Judging-Hold-Brueche verhindern; dieser Hook macht daraus Mechanik: User-Locks (LOCK.user.txt,
LOCK.user.<scope>.txt) entfernt nur der Nutzer, und solange einer im Repo-Root liegt, geht kein Push.
Fail-open bei allem, was kein `git push` ist oder nicht zu einem Repo aufgeloest werden kann.

ERWEITERT 2026-09-03 (claude-code@WORKSTATION-LG) um die Lockart LOCK.until(.<scope>).txt
(Fristsperre, lock-master v1.6.0). Anlass ist eine echte Regression, kein Wunsch nach mehr
Reichweite: Der Sentinel-Fleet-Wettbewerbslock - der ANLASSFALL dieser ganzen Regel - wurde am
2026-09-03 01:20 von LOCK.user.* auf LOCK.until.* umgestellt, weil die neue Lockart seinen festen
Freigabetermin maschinell ausdrueckt. Damit fiel er aus dem Guard heraus, der nur LOCK.user*
kannte: der Hook haette den einen Push durchgelassen, den zu verhindern er gebaut wurde.
Until-Locks werden fail-closed behandelt - fehlt oder faellt 'not_before' unparsbar aus, blockiert
der Guard (lock_utils.lock_not_before dokumentiert dieselbe Regel: None heisst "laeuft nie ab",
nie "abgelaufen"). Ist der Zeitpunkt erreicht, blockiert der Lock nicht mehr, obwohl seine Datei
liegen bleibt - Zeitablauf und Loeschschutz sind bei dieser Art getrennt.

Bewusst NICHT einbezogen: Agenten-Arbeitslocks (LOCK.claude.*, LOCK.codex.*, LOCK.team.*). Die
sperren andere Agenten aus, nicht ihren eigenen Besitzer - ein Push-Guard darauf wuerde genau den
aussperren, der gerade legitim arbeitet. Zu LOCK.condition.* siehe Delta vom 2026-09-03 (offen).

ERWEITERT 2026-09-03 (lock-worker) um das Feld 'release_mode' (T-20260903-476807738, dritte
Auflage desselben Musters: eine neue Lockart-Semantik ist erst fertig, wenn das Werkzeug, das
sie durchsetzen soll, sie mitkennt). Setzt ein Until-Lock ZUSAETZLICH 'release_condition', darf
die Frist allein nur noch bei 'release_mode: any' freigeben - der Default 'all' kann die
Freitext-Bedingung nicht selbst pruefen und blockiert daher fail-closed weiter, bis der
Nutzer/ein Agent die Datei entfernt.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

PUSH = re.compile(r"\bgit\b[^\r\n|;&]*\bpush\b", re.IGNORECASE)
# `cd <pfad>` und `git -C <pfad>`: Pfade in Anfuehrungszeichen oder ohne
PATHS = re.compile(r"(?:\bcd\s+|\bgit\s+-C\s+)(?:\"([^\"]+)\"|'([^']+)'|(\S+))")
LOCK_GLOBS = ("LOCK.user*.txt", "LOCK.until*.txt")
UNTIL_RE = re.compile(r"^LOCK\.until(\.|$)", re.IGNORECASE)
NOT_BEFORE_RE = re.compile(r"^\s*not_before\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
RELEASE_CONDITION_RE = re.compile(r"^\s*release_condition\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
RELEASE_MODE_RE = re.compile(r"^\s*release_mode\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def payload_from_stdin() -> dict:
    try:
        return json.load(sys.stdin) or {}
    except (json.JSONDecodeError, OSError, TypeError):
        return {}


def to_path(raw: str) -> Path | None:
    raw = raw.strip()
    if not raw:
        return None
    m = re.match(r"^/([a-zA-Z])/(.*)$", raw)  # Git-Bash /c/... -> C:/...
    if m:
        raw = f"{m.group(1).upper()}:/{m.group(2)}"
    try:
        return Path(raw).expanduser()
    except (OSError, ValueError):
        return None


def repo_root(start: Path | None) -> Path | None:
    if start is None:
        return None
    try:
        cur = start if start.is_dir() else start.parent
    except OSError:
        return None
    for cand in [cur, *cur.parents]:
        if (cand / ".git").exists():
            return cand
    return None


def until_lock_expired(lock: Path) -> bool:
    """True nur wenn ein Until-Lock den Push nicht mehr sperrt.

    Fail-closed: fehlendes, leeres oder unparsbares 'not_before' gilt als "laeuft nie ab".

    T-20260903-476807738: Setzt die Datei ZUSAETZLICH 'release_condition', entscheidet
    'release_mode' ob die Frist allein reicht. 'all' (Default) kann der Guard nicht selbst
    pruefen (Freitext) -> weiterhin sperren, bis der Nutzer/ein Agent die Datei entfernt.
    Nur 'any' gibt allein durch die Frist frei. Ungueltiger Wert -> 'all' (weiter sperren).
    """
    try:
        text = lock.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    m = NOT_BEFORE_RE.search(text)
    if not m:
        return False
    raw = m.group(1).strip()
    moment_reached = None
    for candidate in (raw, raw.replace(" ", "T", 1)):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        moment_reached = datetime.now() >= parsed
        break
    if not moment_reached:
        return False
    cm = RELEASE_CONDITION_RE.search(text)
    if not cm or not cm.group(1).strip():
        return True
    mm = RELEASE_MODE_RE.search(text)
    mode = mm.group(1).strip().lower() if mm else "all"
    return mode == "any"


# ASCII-Ziffern, begrenzte Laenge. str.isdigit() war hier falsch: es akzeptiert
# auch Unicode-Ziffern wie "²", an denen int() dann mit ValueError scheitert.
FENCE_VALUE_RE = re.compile(r"\A[0-9]{1,19}\Z")
FENCE_RE = re.compile(r"^[ \t]*fence[ \t]*:[ \t]*([0-9]{1,19})[ \t]*$", re.IGNORECASE | re.MULTILINE)
CREATED_RE = re.compile(r"^[ \t]*created[ \t]*:[ \t]*(.+?)[ \t]*$", re.IGNORECASE | re.MULTILINE)
EXPIRES_AFTER_RE = re.compile(r"^[ \t]*expires_after[ \t]*:[ \t]*(.+?)[ \t]*$", re.IGNORECASE | re.MULTILINE)
DURATION_RE = re.compile(r"\A([0-9]{1,6})[ \t]*([smhd])\Z", re.IGNORECASE)
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
# Bibliotheks-Default, wenn expires_after fehlt oder unlesbar ist. Der Hook
# kannte ihn nicht und liess deshalb einen 30 h alten Lock als gueltig
# durchgehen, den lock_utils laengst als verfallen meldet.
DEFAULT_EXPIRES_HOURS = 24


def _last(pattern: re.Pattern, text: str) -> str | None:
    """Letzter Treffer -- der Dict-Parser der Bibliothek gewinnt ebenso."""
    found = pattern.findall(text)
    return found[-1].strip() if found else None


def _lock_expired(text: str, lock: Path) -> bool:
    """Ablauf wie lock_utils.is_expired fuer gewoehnliche Locks.

    Bewusst inklusive der Legacy-Faelle, die der Hook vorher nicht kannte und
    deshalb als "gilt noch" durchwinkte (Codex-Review PR#8, Punkt 3):
      expires_after fehlt oder ist kaputt -> Default 24 h, nicht "unbegrenzt"
      created fehlt oder ist kaputt       -> Datei-mtime, nicht "unbegrenzt"
    Ist der Ablauf nicht bestimmbar, gilt der Anspruch als verloren -- ein
    Zweifel darf hier keinen Besitznachweis erzeugen.
    """
    raw_expires = _last(EXPIRES_AFTER_RE, text)
    delta = timedelta(hours=DEFAULT_EXPIRES_HOURS)
    if raw_expires:
        m = DURATION_RE.match(raw_expires)
        if m:
            delta = timedelta(**{_UNITS[m.group(2).lower()]: int(m.group(1))})

    created = None
    raw_created = _last(CREATED_RE, text)
    if raw_created:
        candidate = raw_created.replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                created = datetime.strptime(candidate, fmt)
                break
            except ValueError:
                continue
    if created is None:
        try:
            created = datetime.fromtimestamp(lock.stat().st_mtime)
        except OSError:
            return True  # Zustand nicht feststellbar -> kein Besitznachweis
    return datetime.now() > created + delta


def fence_lost_reason(lock_raw: str | None, my_fence: str, lock: Path) -> str | None:
    """Fencing-Gegenprobe des Guards. None = Anspruch gilt noch, sonst der Grund.

    Kanonisch ist lock_utils.fence_status(); dies ist die hook-lokale
    Nachbildung, weil dieser Hook bewusst ohne Projekt-Imports auskommt --
    dieselbe Bauart wie schon bei not_before/release_mode oben. Sie muss
    dieselben Urteile faellen; wo sie milder war, hat sie einen Besitznachweis
    erzeugt, den die Bibliothek verweigert.

    Fail-closed: alles, was nicht nachweislich noch der eigene Anspruch ist,
    gilt als verloren. Ohne LOCK_FENCE aendert sich nichts am Altverhalten.
    """
    if not FENCE_VALUE_RE.match(my_fence):
        return f"LOCK_FENCE={my_fence[:40]!r} ist keine gueltige Vergabenummer"
    if lock_raw is None:
        return "die Lock-Datei aus LOCK_FENCE_FILE existiert nicht oder ist unlesbar"
    current = _last(FENCE_RE, lock_raw)
    if current is None:
        return "die Lock-Datei traegt keine Vergabenummer (von einem aelteren Schreiber neu angelegt)"
    if int(current) != int(my_fence):
        richtung = "weitergegeben" if int(current) > int(my_fence) else "zurueckgesetzt"
        return f"die Sperre wurde {richtung}: fence {current} statt {my_fence}"
    # Eigener Anspruch, aber abgelaufen: die Datei liegt noch da, das Lease
    # nicht mehr. Genau der Fall aus T-20260920-692115839, bevor prune raeumt.
    if _lock_expired(lock_raw, lock):
        return "der eigene Anspruch ist abgelaufen (TTL verstrichen) -- erneuern statt weiterschreiben"
    return None


def fence_violation(roots: list[Path]) -> str | None:
    """Wertet LOCK_FENCE/LOCK_FENCE_FILE aus. None = kein Einwand.

    `roots` sind die Repositories, um die es bei diesem Push tatsaechlich geht.
    Die genannte Lockdatei muss in einem davon liegen: ohne diese Bindung war
    jede beliebige Datei mit dem Inhalt "fence: <meine nummer>" ein gueltiger
    Besitznachweis, auch weit ausserhalb des geschuetzten Repos
    (Codex-Review PR#8, Punkt 4). Die Variable bleibt eine Selbstauskunft --
    was sie nicht mehr sein darf, ist eine Selbstauskunft ueber ein FREMDES
    Verzeichnis.
    """
    my_fence = (os.environ.get("LOCK_FENCE") or "").strip()
    if not my_fence:
        return None  # kein Fence deklariert -> Altverhalten, nichts aendert sich
    lock_file = (os.environ.get("LOCK_FENCE_FILE") or "").strip()
    if not lock_file:
        return "LOCK_FENCE ist gesetzt, LOCK_FENCE_FILE nicht -- der Anspruch ist nicht pruefbar"
    path = to_path(lock_file)
    if path is None:
        return f"LOCK_FENCE_FILE={lock_file[:120]!r} ist kein brauchbarer Pfad"
    try:
        resolved = path.resolve()
    except (OSError, ValueError):
        return "LOCK_FENCE_FILE laesst sich nicht aufloesen"
    if roots and not any(_within(resolved, r) for r in roots):
        return (f"LOCK_FENCE_FILE zeigt nach {resolved} -- ausserhalb des Repos, "
                "um das es hier geht; das belegt keinen Anspruch darauf")
    try:
        raw = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raw = None
    return fence_lost_reason(raw, my_fence, resolved)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def blocking_locks(root: Path) -> list[Path]:
    """Aktive Locks im Repo-Root, die einen Push sperren: LOCK.user* immer, LOCK.until* bis zur Frist."""
    found: list[Path] = []
    for pattern in LOCK_GLOBS:
        try:
            found.extend(root.glob(pattern))
        except OSError:
            continue
    blocking = [
        lock for lock in found
        if not (UNTIL_RE.match(lock.name) and until_lock_expired(lock))
    ]
    return sorted(set(blocking))


def main() -> int:
    payload = payload_from_stdin()
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command", "")
    if not isinstance(command, str) or not PUSH.search(command):
        return 0
    candidates: list[Path] = []
    cwd = to_path(str(payload.get("cwd", "")))
    if cwd:
        candidates.append(cwd)
    for m in PATHS.finditer(command):
        p = to_path(next(g for g in m.groups() if g))
        if p:
            candidates.append(p)
    roots = {}
    for c in candidates:
        r = repo_root(c)
        if r:
            roots[str(r)] = r
    hits = [(r, blocking_locks(r)) for r in roots.values()]
    hits = [(r, locks) for r, locks in hits if locks]

    # REIHENFOLGE IST SCHUTZ: erst der bestehende User-/Until-Lock, dann das
    # neue Fencing. Vorher lief die Fencing-Pruefung zuerst, und eine Ausnahme
    # darin (ValueError aus int("²")) beendete den Guard mit Exit 1 -- fuer
    # PreToolUse ist nur Exit 2 blockierend, also fiel der ALTE Schutz aus, den
    # dieser Hook eigentlich traegt (Codex-Review PR#8, Punkt 4). Ein neuer
    # Zusatz darf den Bestand nie aushebeln koennen.
    if hits:
        lines = ["LOCK-PUSH-GUARD: git push blockiert - aktiver Lock im Repo (User-Lock: nur der Nutzer entfernt ihn; Until-Lock: bis not_before):"]
        for r, locks in hits:
            for lock in locks:
                lines.append(f"  {lock}")
        print("\n".join(lines), file=sys.stderr)
        return 2

    # Fencing (T-20260920-692115839): Ein Halter, der laenger als seine TTL
    # stillstand, merkt ohne Vergabenummer nicht, dass die Sperre weiterging.
    # Greift nur, wenn diese Session eine Nummer deklariert hat.
    violation = fence_violation(list(roots.values()))
    if violation:
        print("LOCK-PUSH-GUARD: git push blockiert - die eigene Sperre gilt nicht mehr:",
              file=sys.stderr)
        print(f"  {violation}", file=sys.stderr)
        return 2
    return 0


def _selftest() -> None:
    """python lock_push_guard.py --selftest  (legt nichts Bleibendes an)"""
    import tempfile
    import subprocess

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        sub = repo / "sub"
        sub.mkdir()

        def run(cmd: str, cwd: str, env: dict | None = None) -> int:
            child_env = dict(os.environ)
            child_env.pop("LOCK_FENCE", None)
            child_env.pop("LOCK_FENCE_FILE", None)
            child_env.update(env or {})
            proc = subprocess.run(
                [sys.executable, __file__],
                input=json.dumps({"tool_input": {"command": cmd}, "cwd": cwd}),
                capture_output=True, text=True, env=child_env,
            )
            return proc.returncode

        assert run("git push origin main", str(sub)) == 0, "ohne Lock muss Push durch"
        user_lock = repo / "LOCK.user.judging.txt"
        user_lock.write_text("x", encoding="utf-8")
        assert run("git push origin main", str(sub)) == 2, "mit Lock via cwd blockieren"
        assert run(f'cd "{repo}" && git push', "C:/") == 2, "mit Lock via cd-Pfad blockieren"
        assert run("git status", str(sub)) == 0, "kein push -> durch"
        assert run("git push", "C:/") == 0, "unbekanntes Repo -> fail-open"
        user_lock.unlink()

        # Until-Locks (Fristsperre) - der Sentinel-Fleet-Fall, siehe Modul-Docstring
        until = repo / "LOCK.until.winners-announcement.txt"
        year = datetime.now().year

        until.write_text(f"not_before: {year + 5}-10-08T12:00-07:00\n", encoding="utf-8")
        assert run("git push", str(sub)) == 2, "Frist noch nicht erreicht -> blockieren"

        until.write_text(f"not_before: {year - 5}-10-08T12:00-07:00\n", encoding="utf-8")
        assert run("git push", str(sub)) == 0, "Frist verstrichen -> Push frei, Datei bleibt liegen"

        until.write_text("purpose: kein not_before-Feld\n", encoding="utf-8")
        assert run("git push", str(sub)) == 2, "fehlendes not_before -> fail-closed"

        until.write_text("not_before: sobald der Wettbewerb vorbei ist\n", encoding="utf-8")
        assert run("git push", str(sub)) == 2, "unparsbares not_before -> fail-closed"

        until.write_text(f"not_before: {year + 5}-10-08 12:00\n", encoding="utf-8")
        assert run("git push", str(sub)) == 2, "ISO ohne Offset, Leerzeichen statt T -> lesbar"
        until.unlink()

        # release_mode (T-20260903-476807738): Frist allein reicht nur bei 'any'.
        gated = repo / "LOCK.until.gated.txt"
        gated.write_text(
            f"not_before: {year - 5}-10-08T12:00-07:00\nrelease_condition: review notes incorporated\n",
            encoding="utf-8",
        )
        assert run("git push", str(sub)) == 2, (
            "Frist abgelaufen + Bedingung offen + mode=all (Default) -> MUSS weiter blockieren"
        )
        gated.write_text(
            f"not_before: {year - 5}-10-08T12:00-07:00\nrelease_condition: x\nrelease_mode: all\n",
            encoding="utf-8",
        )
        assert run("git push", str(sub)) == 2, "explizites mode=all verhaelt sich wie Default"
        gated.write_text(
            f"not_before: {year - 5}-10-08T12:00-07:00\nrelease_condition: x\nrelease_mode: any\n",
            encoding="utf-8",
        )
        assert run("git push", str(sub)) == 0, "mode=any -> Frist allein genuegt"
        gated.write_text(
            f"not_before: {year - 5}-10-08T12:00-07:00\nrelease_condition: x\nrelease_mode: whenever\n",
            encoding="utf-8",
        )
        assert run("git push", str(sub)) == 2, "ungueltiger release_mode -> fail-closed wie all"
        gated.unlink()

        # Agenten-Arbeitslocks sperren andere Agenten aus, nicht den eigenen Besitzer
        agent_lock = repo / "LOCK.claude.route-mirror.txt"
        agent_lock.write_text("owner: claude-code\n", encoding="utf-8")
        assert run("git push", str(sub)) == 0, "Agenten-Lock darf den Besitzer nicht aussperren"
        agent_lock.unlink()

        # --- Fencing (T-20260920-692115839) --------------------------------
        # Ohne deklarierte Nummer aendert sich nichts. Mit Nummer muss der Guard
        # merken, dass die Sperre weitergegeben wurde oder der eigene Anspruch
        # abgelaufen ist -- der Fall, den TTL allein nicht abdeckt.
        fenced = repo / "LOCK.work.txt"
        fresh = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        stale = (datetime.now() - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%S")

        def write_lock(owner: str, created: str, fence: str | None) -> None:
            body = f"owner: {owner}\ncreated: {created}\nexpires_after: 24h\n"
            if fence is not None:
                body += f"fence: {fence}\n"
            fenced.write_text(body, encoding="utf-8")

        mine = {"LOCK_FENCE": "1000", "LOCK_FENCE_FILE": str(fenced)}

        write_lock("A", fresh, "1000")
        assert run("git push", str(sub)) == 0, "ohne LOCK_FENCE bleibt das Altverhalten"
        assert run("git push", str(sub), mine) == 0, "eigene gueltige Nummer -> Push frei"

        write_lock("B", fresh, "2000")
        assert run("git push", str(sub), mine) == 2, "Sperre weitergegeben -> blockieren"

        fenced.unlink()
        assert run("git push", str(sub), mine) == 2, "eigene Lock-Datei weg -> blockieren"

        write_lock("A", stale, "1000")
        assert run("git push", str(sub), mine) == 2, (
            "TTL verstrichen -> blockieren, auch wenn die Datei noch daliegt"
        )

        write_lock("A", fresh, None)
        assert run("git push", str(sub), mine) == 2, (
            "Lock ohne Nummer, aber eigener Anspruch deklariert -> fail-closed"
        )

        assert run("git push", str(sub), {"LOCK_FENCE": "1000"}) == 2, (
            "LOCK_FENCE ohne LOCK_FENCE_FILE -> nicht pruefbar -> fail-closed"
        )
        fenced.unlink()

        # --- Codex-Review PR#8: die vier belegten Luecken ------------------
        # Jede Zeile hier stand vorher als reproduzierter Fehlschlag im
        # Reviewbericht. Sie sind der Grund, dass der Guard umgebaut wurde.

        # (a) Der bestehende User-Lock-Schutz darf durch KEINEN Fehler in der
        # neuen Fencing-Pruefung ausfallen. Gemessen wurde damals:
        # user_lock_exists=true exit_code=1 -- und Exit 1 blockiert bei
        # PreToolUse nicht. "\u00b2".isdigit() ist True, int("\u00b2") wirft ValueError.
        user_lock = repo / "LOCK.user.judging.txt"
        user_lock.write_text("owner: lukas\n", encoding="utf-8")
        for bad in ("\u00b2", "9" * 5000, "", "  ", "12a", "-1", "0x10"):
            assert run("git push", str(sub),
                       {"LOCK_FENCE": bad, "LOCK_FENCE_FILE": str(fenced)}) == 2, (
                f"User-Lock MUSS blockieren, auch bei LOCK_FENCE={bad[:12]!r}"
            )
        user_lock.unlink()

        # (b) Dieselben Werte ohne User-Lock: fail-closed statt Absturz.
        # Leer = "nicht deklariert" -> Altverhalten, also frei.
        write_lock("A", fresh, "1000")
        for bad in ("\u00b2", "9" * 5000, "12a", "-1", "0x10"):
            assert run("git push", str(sub),
                       {"LOCK_FENCE": bad, "LOCK_FENCE_FILE": str(fenced)}) == 2, (
                f"ungueltiges LOCK_FENCE={bad[:12]!r} -> blockieren, nicht abstuerzen"
            )
        assert run("git push", str(sub),
                   {"LOCK_FENCE": "", "LOCK_FENCE_FILE": str(fenced)}) == 0, (
            "leeres LOCK_FENCE = nicht deklariert -> Altverhalten"
        )

        # (c) LOCK_FENCE_FILE muss zum Repo gehoeren, um das es geht. Eine
        # beliebige Datei irgendwo mit "fence: 1000" war vorher ein gueltiger
        # Besitznachweis fuer ein fremdes Repo.
        elsewhere = Path(tmp) / "woanders" / "LOCK.work.txt"
        elsewhere.parent.mkdir()
        write = f"owner: A\ncreated: {fresh}\nexpires_after: 24h\nfence: 1000\n"
        elsewhere.write_text(write, encoding="utf-8")
        assert run("git push", str(sub),
                   {"LOCK_FENCE": "1000", "LOCK_FENCE_FILE": str(elsewhere)}) == 2, (
            "Lockdatei ausserhalb des Repos belegt keinen Anspruch darauf"
        )

        # (d) Ablaufsemantik der Bibliothek: Legacy-Faelle, die der Hook vorher
        # als gueltig durchwinkte, obwohl lock_utils sie als verfallen meldet.
        for body, warum in (
            (f"owner: A\ncreated: {stale}\nfence: 1000\n",
             "expires_after fehlt -> Default 24h, 30h alt = abgelaufen"),
            (f"owner: A\ncreated: {stale}\nexpires_after: broken\nfence: 1000\n",
             "kaputtes expires_after -> Default 24h, nicht unbegrenzt"),
            (f"owner: A\nexpires_after: 24h\nfence: 1000\n",
             "created fehlt -> mtime-Fallback, nicht unbegrenzt"),
            (f"owner: A\ncreated: kaputt\nexpires_after: 24h\nfence: 1000\n",
             "kaputtes created -> mtime-Fallback"),
        ):
            fenced.write_text(body, encoding="utf-8")
            if "created" not in body or "kaputt" in body:
                old_time = (datetime.now() - timedelta(hours=30)).timestamp()
                os.utime(fenced, (old_time, old_time))
            assert run("git push", str(sub), mine) == 2, warum

        # (e) Parser-Paritaet bei doppelten Feldern: der Dict-Parser der
        # Bibliothek nimmt den LETZTEN Wert, das Regex nahm den ersten.
        fenced.write_text(
            f"owner: A\ncreated: {fresh}\nexpires_after: 24h\nfence: 1000\nfence: 2000\n",
            encoding="utf-8")
        assert run("git push", str(sub), mine) == 2, (
            "doppeltes fence -> letzter Wert gilt (2000), Anspruch 1000 ist verloren"
        )
        fenced.unlink()
    print("selftest OK")


def guarded_main() -> int:
    """main() mit fail-closed Abschluss.

    Ein unerwarteter Fehler im Guard darf nicht wie "kein Lock" aussehen. Exit 1
    ist fuer PreToolUse NICHT blockierend (nur Exit 2 ist es), ein Absturz waere
    also eine stille Freigabe gewesen -- genau der Weg, auf dem eine kaputte
    Umgebungsvariable den User-Lock-Schutz ausfallen liess.
    """
    try:
        return main()
    except Exception as exc:  # noqa: BLE001 - im Zweifel sperren, nie durchlassen
        print("LOCK-PUSH-GUARD: git push blockiert - der Guard selbst ist gescheitert "
              f"({type(exc).__name__}: {exc}). Im Zweifel wird gesperrt.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        raise SystemExit(guarded_main())
