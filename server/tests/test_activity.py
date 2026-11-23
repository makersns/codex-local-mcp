"""Synthetic metadata, bounded-memory and concurrency checks."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import unittest
from types import SimpleNamespace

from sugar_local_mcp.activity import ActivityTracker


def context(identifier=None):
    meta = {} if identifier is None else {"openai/session": identifier}
    return SimpleNamespace(request_context=SimpleNamespace(meta=meta))


class Clock:
    def __init__(self):
        self.monotonic = 1000.0
        self.wall = 1700000000.0

    def advance(self, seconds):
        self.monotonic += seconds
        self.wall += seconds


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.tracker = self.make()

    def make(self, **kwargs):
        return ActivityTracker(monotonic=lambda: self.clock.monotonic,
                               wall_clock=lambda: self.clock.wall, **kwargs)

    def test_sequence_deduplicates_and_does_not_retain_or_return_raw_identifiers(self):
        for identifier in ("fixture-conversation-a", "fixture-conversation-a", "fixture-conversation-b"):
            call = self.tracker.begin(context(identifier), "read_file", "project")
            self.tracker.finish(call)
        state = self.tracker.snapshot()
        self.assertEqual((state["active_calls"], state["active_sessions"], state["recent_sessions"]), (0, 0, 2))
        self.assertTrue(state["coverage_complete"])
        self.assertTrue(state["metadata_seen"])
        self.assertEqual([row["number"] for row in state["sessions"]], [1, 2])
        self.assertNotIn("fixture-conversation", json.dumps(state))
        self.assertNotIn("fixture-conversation", repr(vars(self.tracker)))
        self.assertTrue(all(isinstance(key, bytes) and len(key) == 32
                            for key in self.tracker._sessions))

    def test_concurrent_calls_count_unique_sessions_and_finish_once(self):
        entered = threading.Barrier(17)
        release = threading.Event()
        def worker(index):
            call = self.tracker.begin(context("fixture-" + str(index % 4)), "read_file", "project")
            try:
                entered.wait(timeout=3)
                release.wait(3)
            finally:
                self.tracker.finish(call)
                self.tracker.finish(call)
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(worker, index) for index in range(16)]
            try:
                entered.wait(timeout=3)
                state = self.tracker.snapshot()
                self.assertEqual(state["active_calls"], 16)
                self.assertEqual(state["active_sessions"], 4)
                self.assertEqual(state["recent_sessions"], 4)
                self.assertEqual(sum(row["active_calls"] for row in state["sessions"]), 16)
            finally:
                release.set()
            for future in futures:
                future.result(timeout=3)
        self.assertEqual(self.tracker.snapshot()["active_calls"], 0)
        self.assertEqual(self.tracker.snapshot()["active_sessions"], 0)

    def test_missing_invalid_and_oversized_metadata_is_unidentified_without_rejection(self):
        values = [None, "", "  ", True, 3, [], {}, "x" * 1025, "汉" * 342, "bad\nid", "\ud800"]
        for value in values:
            call = self.tracker.begin(context(value), "workspace_list")
            self.tracker.finish(call)
        call = self.tracker.begin(SimpleNamespace(request_context=SimpleNamespace(meta=[])), "workspace_list")
        self.tracker.finish(call)
        call = self.tracker.begin(None, "workspace_list")
        self.tracker.finish(call)
        state = self.tracker.snapshot()
        self.assertEqual(state["recent_sessions"], 0)
        self.assertEqual(state["active_calls"], 0)
        self.assertEqual(state["unknown_active_calls"], 0)
        self.assertEqual(state["unknown_recent_calls"], len(values) + 2)
        self.assertFalse(state["metadata_seen"])
        self.assertFalse(state["coverage_complete"])
        self.assertFalse(state["capacity_limited"])

    def test_long_calls_stay_recent_and_monotonic_window_ignores_wall_clock_changes(self):
        known = self.tracker.begin(context("long-fixture"), "read_file")
        unknown = self.tracker.begin(context(), "read_file")
        self.clock.advance(601)
        self.clock.wall -= 86400 * 365
        state = self.tracker.snapshot()
        self.assertEqual(state["active_sessions"], 1)
        self.assertEqual(state["recent_sessions"], 1)
        self.assertEqual(state["unknown_active_calls"], 1)
        self.assertEqual(state["unknown_recent_calls"], 0)
        self.assertFalse(state["coverage_complete"])
        self.tracker.finish(known)
        self.tracker.finish(unknown)
        state = self.tracker.snapshot()
        self.assertEqual(state["recent_sessions"], 0)
        self.assertEqual(state["active_calls"], 0)
        self.assertTrue(state["coverage_complete"])
        short = self.tracker.begin(context("new-fixture"), "read_file")
        self.tracker.finish(short)
        self.clock.wall += 86400 * 1000
        self.assertEqual(self.tracker.snapshot()["recent_sessions"], 1)
        self.clock.advance(600)
        self.assertEqual(self.tracker.snapshot()["recent_sessions"], 0)

    def test_all_active_capacity_is_partial_without_mislabeling_known_metadata(self):
        tracker = self.make(max_sessions=2)
        first = tracker.begin(context("fixture-a"), "read_file")
        second = tracker.begin(context("fixture-b"), "read_file")
        overflow = tracker.begin(context("fixture-c"), "read_file")
        state = tracker.snapshot()
        self.assertEqual((state["active_sessions"], state["active_calls"]), (2, 3))
        self.assertEqual(state["unknown_active_calls"], 0)
        self.assertTrue(state["capacity_limited"])
        self.assertFalse(state["coverage_complete"])
        self.assertEqual(len(tracker._sessions), 2)
        tracker.finish(first)
        tracker.finish(second)
        self.clock.advance(601)
        self.assertTrue(tracker.snapshot()["capacity_limited"])
        tracker.finish(overflow)
        state = tracker.snapshot()
        self.assertFalse(state["capacity_limited"])
        self.assertTrue(state["coverage_complete"])
        self.assertEqual(state["active_calls"], 0)

    def test_idle_eviction_preserves_active_sessions_and_clears_after_window(self):
        tracker = self.make(max_sessions=2)
        active = tracker.begin(context("active-fixture"), "read_file")
        idle = tracker.begin(context("idle-fixture"), "read_file")
        tracker.finish(idle)
        self.clock.advance(1)
        newest = tracker.begin(context("new-fixture"), "read_file")
        tracker.finish(newest)
        state = tracker.snapshot()
        self.assertEqual(state["recent_sessions"], 2)
        self.assertEqual(state["active_sessions"], 1)
        self.assertEqual(state["sessions"][0]["number"], 1)
        self.assertTrue(state["capacity_limited"])
        tracker.finish(active)
        self.clock.advance(601)
        self.assertEqual(tracker.snapshot()["recent_sessions"], 0)
        self.assertFalse(tracker.snapshot()["capacity_limited"])

    def test_unknown_window_is_bounded_and_marks_capacity_loss(self):
        tracker = self.make(max_unknown_events=2)
        for _ in range(3):
            call = tracker.begin(context(), "read_file")
            tracker.finish(call)
        state = tracker.snapshot()
        self.assertEqual(state["unknown_recent_calls"], 2)
        self.assertTrue(state["capacity_limited"])
        self.assertFalse(state["coverage_complete"])
        self.assertEqual(len(tracker._unknown_starts), 2)
        self.clock.advance(600)
        state = tracker.snapshot()
        self.assertEqual(state["unknown_recent_calls"], 0)
        self.assertFalse(state["capacity_limited"])
        self.assertTrue(state["coverage_complete"])

    def test_restart_changes_anonymization_and_resets_statistics(self):
        first = self.tracker.begin(context("same-fictional-id"), "workspace_list")
        self.tracker.finish(first)
        other = self.make()
        self.assertNotEqual(self.tracker.snapshot()["instance_id"], other.snapshot()["instance_id"])
        self.assertEqual(other.snapshot()["recent_sessions"], 0)
        self.assertIsNone(other.snapshot()["last_activity_at"])
        second = other.begin(context("same-fictional-id"), "workspace_list")
        other.finish(second)
        self.assertNotEqual(next(iter(self.tracker._sessions)), next(iter(other._sessions)))
        self.assertEqual(other.snapshot()["sessions"][0]["number"], 1)

    def test_detail_rows_are_bounded_without_changing_complete_aggregate(self):
        for index in range(40):
            call = self.tracker.begin(context("fixture-" + str(index)), "read_file", "/private/not-a-workspace")
            self.tracker.finish(call)
        before = self.tracker.snapshot()
        for _ in range(5):
            state = self.tracker.snapshot(running_tasks=3)
            self.assertEqual(state["recent_sessions"], 40)
            self.assertEqual(state["active_calls"], 0)
            self.assertEqual(state["running_tasks"], 3)
            self.assertEqual(len(state["sessions"]), 32)
            self.assertTrue(state["sessions_truncated"])
            self.assertTrue(state["coverage_complete"])
            self.assertIsNone(state["last_workspace"])
            self.assertEqual(state["last_activity_at"], before["last_activity_at"])
        self.assertNotIn("/private/", json.dumps(state))

    def test_invalid_internal_configuration_cannot_create_unbounded_registry(self):
        for capacity in (0, -1, 257, True):
            with self.assertRaises(ValueError):
                self.make(max_sessions=capacity)
        with self.assertRaises(ValueError):
            self.tracker.begin(context("fixture"), "untrusted operation")
        self.assertEqual(self.tracker.snapshot()["active_calls"], 0)


if __name__ == "__main__":
    unittest.main()
