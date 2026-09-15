#!/usr/bin/env python3
"""status_probe JSON merge: omitted keys must not overwrite log truth."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from game_server.monitor import MonitorState  # noqa: E402
from game_server.status_probe import (  # noqa: E402
    apply_status_probe,
    parse_status_probe_stdout,
)


class ParseStatusProbeTests(unittest.TestCase):
    def test_empty_and_invalid(self) -> None:
        self.assertIsNone(parse_status_probe_stdout(""))
        self.assertIsNone(parse_status_probe_stdout("not json"))
        self.assertIsNone(parse_status_probe_stdout("[1]"))

    def test_last_line_object(self) -> None:
        payload = parse_status_probe_stdout('noise\n{"player_count": 0}\n')
        self.assertEqual(payload, {"player_count": 0})


class ApplyStatusProbeTests(unittest.TestCase):
    def test_empty_object_does_not_change_count(self) -> None:
        state = MonitorState()
        state.player_count = 3
        state.players_known = True
        apply_status_probe(state, {})
        self.assertEqual(state.player_count, 3)
        self.assertTrue(state.players_known)

    def test_zero_is_a_real_claim(self) -> None:
        state = MonitorState()
        state.player_count = 3
        state.players_known = True
        state.players.add("Ada")
        apply_status_probe(state, {"player_count": 0})
        self.assertEqual(state.player_count, 0)
        self.assertTrue(state.players_known)
        self.assertEqual(state.players, set())

    def test_null_and_omit_leave_log_derived_count(self) -> None:
        state = MonitorState()
        state.player_count = 3
        state.players_known = True
        apply_status_probe(state, {"player_count": None, "ready": True})
        self.assertEqual(state.player_count, 3)
        self.assertTrue(state.ready)
        apply_status_probe(state, {"game_version": "1.21.1"})
        self.assertEqual(state.player_count, 3)
        self.assertEqual(state.game_version, "1.21.1")

    def test_invalid_payload_is_ignored(self) -> None:
        state = MonitorState()
        state.player_count = 3
        apply_status_probe(state, None)
        apply_status_probe(state, ["player_count", 0])
        self.assertEqual(state.player_count, 3)


class OccupancyTests(unittest.TestCase):
    def test_asserted_count_outranks_stale_join_names(self) -> None:
        state = MonitorState()
        state.players.add("Ada")
        state.players_known = True
        state.player_count = 1
        apply_status_probe(state, {"player_count": 2})
        self.assertEqual(state.occupancy(), 2)
        self.assertEqual(state.to_dict()["player_count"], 2)
        self.assertTrue(state.to_dict()["players_present"])
        self.assertEqual(state.players, {"Ada"})

    def test_probe_zero_clears_stale_names_and_is_empty(self) -> None:
        state = MonitorState()
        state.players.add("Ada")
        state.players_known = True
        state.player_count = 1
        apply_status_probe(state, {"player_count": 0})
        self.assertEqual(state.occupancy(), 0)
        self.assertEqual(state.players, set())
        self.assertFalse(state.to_dict()["players_present"])

    def test_zero_count_ignores_players_list_in_same_payload(self) -> None:
        state = MonitorState()
        apply_status_probe(state, {"player_count": 0, "players": ["Ada"]})
        self.assertEqual(state.occupancy(), 0)
        self.assertEqual(state.players, set())

    def test_named_joins_cannot_undercount_asserted_headcount(self) -> None:
        state = MonitorState()
        apply_status_probe(state, {"player_count": 3})
        state.players.add("Ada")
        self.assertEqual(state.occupancy(), 3)


if __name__ == "__main__":
    unittest.main()
