# reference/

Versionierte Kopien von Dateien, die **ausserhalb** dieses Repos laufen, aber zu
seinem Verfahren gehoeren.

## lock_push_guard.py

Live-Ort: `~/.claude/hooks/lock_push_guard.py` (kanonische Kopie fuer den
Cross-Host-Abgleich: `<OneDrive>/.SYNC/hooks/lock_push_guard.py`).

**Warum hier eine Kopie liegt.** Die Datei setzt das Lock-System durch, wird aber
nicht mit ihm ausgeliefert. Das Codex-Review zu PR #8 hat daraus einen konkreten
Befund gemacht: Der Hook war bereits geaendert und live, gehoerte aber zu keiner
der versionierten PR-Dateien -- ein Nicht-Merge haette die Aenderung nicht
zurueckgenommen, und ein Review konnte nicht sehen, was tatsaechlich lief.

Diese Kopie ist **Nachweis, nicht Quelle**. Geaendert wird am Live-Ort; danach
wird hierher und nach `.SYNC/hooks/` gespiegelt. Weichen die drei voneinander ab,
gilt der Live-Ort -- und die Abweichung ist ein Pflegefehler, der zu melden ist.

Pruefen: `python reference/lock_push_guard.py --selftest`
