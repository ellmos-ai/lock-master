"""Tests for contested.py -- resolving simultaneous claims over a synced folder."""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import contested

import lock_create
import lock_utils


def _write_team_lock(project: Path, host: str, scope: str | None = None,
                     created: datetime | None = None, expires: str = "24h") -> Path:
    """Team lock with a controllable timestamp (seconds matter for the tiebreak)."""
    name = f"LOCK.team.{scope}.{host}.txt" if scope else f"LOCK.team.{host}.txt"
    stamp = (created or datetime.now()).strftime("%Y-%m-%dT%H:%M:%S")
    path = project / name
    path.write_text(
        f"owner: agent/{host}\ncreated: {stamp}\nhost: {host}\n"
        f"expires_after: {expires}\nmode: hard\nscope: {scope or 'project'}\n",
        encoding="utf-8",
    )
    return path


class CloudPressureCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_plain_directory_shows_no_cloud_pressure(self):
        signal = contested.cloud_pressure(self.project)
        self.assertFalse(signal.cloud_backed)

    def test_path_hint_is_the_fallback_on_platforms_without_attributes(self):
        signal = contested.cloud_pressure(Path("/home/x/OneDrive/project"))
        self.assertTrue(signal.cloud_backed)
        self.assertEqual(signal.source, "path-hint")

    def test_external_prober_wins_when_it_answers(self):
        """A FileCommander-style check is asked, never required."""
        signal = contested.cloud_pressure(self.project, prober=lambda p: True)
        self.assertTrue(signal.cloud_backed)
        self.assertEqual(signal.source, "prober")

    def test_a_broken_prober_never_breaks_the_run(self):
        def explode(_path):
            raise RuntimeError("prober down")

        signal = contested.cloud_pressure(self.project, prober=explode)
        self.assertFalse(signal.cloud_backed)  # fell through to the other stages


class ShouldContestCase(unittest.TestCase):
    """Cost/benefit instead of a blanket wait."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_no_cloud_means_no_procedure(self):
        decision = contested.should_contest(self.project, is_automation=True)
        self.assertFalse(decision.contest)
        self.assertIn("kein Cloud-Ordner", decision.reason)

    def test_interactive_use_is_not_worth_the_wait(self):
        decision = contested.should_contest(
            Path("/x/OneDrive/p"), is_automation=False
        )
        self.assertFalse(decision.contest)
        self.assertIn("interaktiv", decision.reason)

    def test_cloud_plus_automation_is_worth_it(self):
        decision = contested.should_contest(
            Path("/x/OneDrive/p"), is_automation=True
        )
        self.assertTrue(decision.contest)

    def test_force_overrides_both_ways(self):
        self.assertTrue(contested.should_contest(self.project, force=True).contest)
        self.assertFalse(
            contested.should_contest(Path("/x/OneDrive/p"), force=False).contest
        )


class ScopeOverlapCase(unittest.TestCase):
    def test_project_lock_overlaps_everything(self):
        self.assertTrue(contested.scopes_overlap(None, "assets"))
        self.assertTrue(contested.scopes_overlap("assets", None))

    def test_identical_scopes_overlap(self):
        self.assertTrue(contested.scopes_overlap("assets", "assets"))

    def test_parent_and_child_overlap(self):
        self.assertTrue(contested.scopes_overlap("assets", "assets.images"))
        self.assertTrue(contested.scopes_overlap("assets.images", "assets"))

    def test_a_shared_prefix_alone_is_not_containment(self):
        """'assets' must not swallow 'assets-backup' -- a character prefix is
        not an ancestor relation (TODO 'Claim-Haertung')."""
        self.assertFalse(contested.scopes_overlap("assets", "assets-backup"))

    def test_siblings_do_not_overlap(self):
        self.assertFalse(contested.scopes_overlap("assets", "docs"))


class ResolveContestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        self.now = datetime.now()

    def tearDown(self):
        self.temp.cleanup()

    def test_a_lone_claim_wins(self):
        mine = _write_team_lock(self.project, "H1", created=self.now)
        result = contested.resolve_contest(self.project, mine, now=self.now)
        self.assertTrue(result.won)

    def test_the_earliest_claim_wins(self):
        _write_team_lock(self.project, "H2", created=self.now - timedelta(seconds=30))
        mine = _write_team_lock(self.project, "H1", created=self.now)
        result = contested.resolve_contest(self.project, mine, now=self.now)
        self.assertFalse(result.won)
        self.assertIn("H2", result.reason)

    def test_a_tie_is_broken_by_host_order_deterministically(self):
        _write_team_lock(self.project, "AAA", created=self.now)
        mine = _write_team_lock(self.project, "ZZZ", created=self.now)
        result = contested.resolve_contest(self.project, mine, now=self.now)
        self.assertFalse(result.won)
        self.assertIn("aaa", result.reason.lower())

    def test_exactly_one_winner_across_a_shared_view(self):
        """The invariant the whole procedure has to carry."""
        locks = [
            _write_team_lock(self.project, host, created=self.now - timedelta(seconds=index))
            for index, host in enumerate(("H1", "H2", "H3"))
        ]
        winners = [
            lock for lock in locks
            if contested.resolve_contest(self.project, lock, now=self.now).won
        ]
        self.assertEqual(len(winners), 1)
        self.assertEqual(lock_utils.lock_host(winners[0]), "H3")  # earliest

    def test_an_expired_own_claim_never_wins(self):
        """Other hosts filtered it out long ago; without this check it would
        still see itself as the earliest claimant -- two winners."""
        mine = _write_team_lock(
            self.project, "H1", created=self.now - timedelta(hours=30), expires="24h"
        )
        _write_team_lock(self.project, "H2", created=self.now)
        result = contested.resolve_contest(self.project, mine, now=self.now)
        self.assertFalse(result.won)
        self.assertIn("abgelaufen", result.reason)

    def test_expired_rivals_do_not_count(self):
        _write_team_lock(
            self.project, "H2", created=self.now - timedelta(hours=30), expires="24h"
        )
        mine = _write_team_lock(self.project, "H1", created=self.now)
        self.assertTrue(contested.resolve_contest(self.project, mine, now=self.now).won)

    def test_non_overlapping_scopes_do_not_compete(self):
        _write_team_lock(self.project, "H2", scope="docs", created=self.now - timedelta(seconds=30))
        mine = _write_team_lock(self.project, "H1", scope="assets", created=self.now)
        self.assertTrue(contested.resolve_contest(self.project, mine, now=self.now).won)

    def test_a_parent_scope_does_compete(self):
        _write_team_lock(self.project, "H2", created=self.now - timedelta(seconds=30))
        mine = _write_team_lock(self.project, "H1", scope="assets", created=self.now)
        self.assertFalse(contested.resolve_contest(self.project, mine, now=self.now).won)

    def test_exclusive_and_user_locks_are_not_treated_as_rivals(self):
        """Only team locks coexist per host; protected categories are never
        overruled by this procedure."""
        (self.project / "LOCK.user.txt").write_text(
            "owner: lukas\ncreated: 2026-08-15T10:00:00\nremovable_by: user\n",
            encoding="utf-8",
        )
        mine = _write_team_lock(self.project, "H1", created=self.now)
        self.assertTrue(contested.resolve_contest(self.project, mine, now=self.now).won)

    def test_quarantine_is_actually_waited_out(self):
        """Skipping the wait re-reads the same unsynchronised view and both
        sides win -- so the wait is the mechanism, not decoration."""
        waited = []
        mine = _write_team_lock(self.project, "H1", created=self.now)
        contested.contest(
            self.project, mine, quarantine_seconds=300, sleeper=waited.append
        )
        self.assertEqual(waited, [300])


class ExclusiveCreateCase(unittest.TestCase):
    """Stage 1: create exclusively instead of check-then-write."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, *extra: str) -> int:
        return lock_create.main(
            [str(self.project), "--owner", "test-agent", "--host", "TESTHOST",
             "--no-contest", *extra]
        )

    def test_second_create_without_force_is_refused(self):
        self.assertEqual(self._run(), 0)
        with self.assertRaises(SystemExit):
            self._run()

    def test_force_still_overwrites(self):
        self.assertEqual(self._run("--purpose", "first"), 0)
        self.assertEqual(self._run("--force", "--purpose", "second"), 0)
        body = (self.project / "LOCK.txt").read_text(encoding="utf-8")
        self.assertIn("second", body)

    def test_created_stamp_is_second_granular(self):
        """Minute granularity pushes near-simultaneous claims into the host
        tiebreak, where the same host loses structurally every time."""
        self._run()
        created = lock_utils.parse_lock_file(self.project / "LOCK.txt")["created"]
        self.assertRegex(created, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
        self.assertIsNotNone(lock_utils._parse_created(created))


if __name__ == "__main__":
    unittest.main()
