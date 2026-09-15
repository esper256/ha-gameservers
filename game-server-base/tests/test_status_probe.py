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


if __name__ == "__main__":
    unittest.main()
