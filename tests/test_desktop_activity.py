"""Native activity snapshots remain useful when the current backend is older."""
import copy
import json
import unittest
from types import SimpleNamespace

import test_desktop_api as fixtures
from sugar_local_mcp.activity import ActivityTracker

desktop = fixtures.desktop
api = fixtures.api


class NativeActivityTests(unittest.TestCase):
    setUp = fixtures.NativeAPITests.setUp
    mock = fixtures.NativeAPITests.mock
    write_fixture = fixtures.NativeAPITests.write_fixture
    request = fixtures.NativeAPITests.request
    snapshot = fixtures.NativeAPITests.snapshot

    def ready(self, reply):
        self.probe.return_value = {"port_in_use": True, "owned_listener": True,
                                   "managed_pid": 4242, "ready": 200}
        backend = desktop.permissions.backend_request
        backend.side_effect = None
        backend.return_value = reply
        return backend

    def payload(self):
        tracker = ActivityTracker()
        context = SimpleNamespace(request_context=SimpleNamespace(meta={"openai/session": "fictional-private-id"}))
        call = tracker.begin(context, "read_file", "frontend")
        tracker.finish(call)
        return {"ok": True, **tracker.snapshot(running_tasks=2)}

    def assert_unavailable(self, activity, reason):
        self.assertFalse(activity["available"])
        self.assertEqual(activity["reason"], reason)
        for name in ("active_sessions", "recent_sessions", "active_calls",
                     "unknown_active_calls", "unknown_recent_calls", "running_tasks",
                     "observed_at", "statistics_started_at", "instance_id",
                     "metadata_seen", "coverage_complete", "capacity_limited"):
            self.assertIsNone(activity[name], name)
        self.assertEqual(activity["sessions"], [])
        self.assertEqual(activity["window_seconds"], 600)
        self.assertTrue(activity["message"])

    def test_stopped_and_isolated_snapshots_never_query_backend_or_display_zero(self):
        self.assert_unavailable(self.snapshot()["activity"], "not_running")
        desktop.permissions.backend_request.assert_not_called()
        isolated = api.DesktopAPI(self.support)
        result = isolated.handle({"action": "snapshot"})
        self.assertTrue(result["ok"])
        self.assert_unavailable(result["data"]["activity"], "isolated")
        desktop.permissions.backend_request.assert_not_called()

    def test_older_backend_or_ipc_failure_preserves_snapshot_without_false_zero(self):
        for reply, reason in [
            ({"ok": False, "error": {"code": "unknown_operation", "message": "fictional-secret"}}, "unsupported"),
            ({"ok": False, "error": {"code": "timeout", "message": "fictional-secret"}}, "unavailable"),
            ([], "invalid_response"),
        ]:
            with self.subTest(reason=reason):
                backend = self.ready(reply)
                snapshot = self.snapshot()
                self.assert_unavailable(snapshot["activity"], reason)
                self.assertEqual(snapshot["workspace_document"], self.document)
                self.assertEqual(snapshot["status"]["state"], "ready")
                self.assertNotIn("fictional-secret", json.dumps(snapshot))
                backend.assert_called_with(self.document, "activity_snapshot", timeout=1.0)
        backend.side_effect = OSError("fictional-secret")
        snapshot = self.snapshot()
        self.assert_unavailable(snapshot["activity"], "unavailable")
        self.assertNotIn("fictional-secret", json.dumps(snapshot))
        self.keychain.return_value.get.assert_not_called()
        self.start.assert_not_called()
        self.stop.assert_not_called()

    def test_available_snapshot_only_passes_aggregate_allowlist(self):
        payload = self.payload()
        payload["raw_session_id"] = "never-return-this-fiction"
        payload["message"] = "never-return-this-fiction"
        payload["sessions"][0]["raw_session_id"] = "never-return-this-fiction"
        payload["sessions"][0]["argv"] = ["/private/fictional-command"]
        self.ready(payload)
        state = self.snapshot()["activity"]
        self.assertTrue(state["available"])
        self.assertEqual(state["recent_sessions"], 1)
        self.assertEqual(state["active_sessions"], 0)
        self.assertEqual(state["running_tasks"], 2)
        self.assertEqual(state["sessions"][0]["last_workspace"], "frontend")
        encoded = json.dumps(state)
        self.assertNotIn("never-return", encoded)
        self.assertNotIn("fictional-private-id", encoded)
        self.assertNotIn("argv", encoded)
        self.assertNotIn("/private/", encoded)

    def test_malformed_aggregate_is_unavailable_without_breaking_parent_snapshot(self):
        originals = self.payload()
        variants = [
            ("active_sessions", True), ("active_calls", -1), ("recent_sessions", "2"),
            ("recent_sessions", 257), ("running_tasks", 2 ** 60),
            ("window_seconds", 600.0), ("window_seconds", 601),
            ("instance_id", "arbitrary-id"), ("observed_at", "not-a-time"),
            ("statistics_started_at", "2026-11-23T10:00:00"),
            ("last_operation", []), ("last_workspace", "/private/fixture"),
            ("metadata_seen", 1), ("sessions", {}), ("coverage_complete", False),
        ]
        for name, value in variants:
            with self.subTest(name=name, value=value):
                payload = {**originals, name: value}
                self.ready(payload)
                self.assert_unavailable(self.snapshot()["activity"], "invalid_response")
        for replacement in ([], {"number": 1, "active_calls": 0, "last_activity_at": originals["observed_at"],
                                "last_operation": [], "last_workspace": "demo"}):
            self.ready({**originals, "sessions": [replacement]})
            self.assert_unavailable(self.snapshot()["activity"], "invalid_response")

    def test_absent_optional_fields_become_null_without_breaking_snapshot(self):
        payload = self.payload()
        for name in ("last_activity_at", "last_operation", "last_workspace"):
            del payload[name]
        self.ready(payload)
        state = self.snapshot()["activity"]
        self.assertTrue(state["available"])
        self.assertEqual(state["recent_sessions"], 1)
        for name in ("last_activity_at", "last_operation", "last_workspace"):
            self.assertIsNone(state[name])

    def test_mixed_sources_stay_partial_and_refresh_does_not_reuse_stale_values(self):
        tracker = ActivityTracker()
        known = tracker.begin(SimpleNamespace(request_context=SimpleNamespace(
            meta={"openai/session": "fiction-a"})), "read_file", "frontend")
        unknown = tracker.begin(None, "workspace_list")
        tracker.finish(known)
        self.ready({"ok": True, **tracker.snapshot()})
        state = self.snapshot()["activity"]
        self.assertTrue(state["available"])
        self.assertEqual(state["recent_sessions"], 1)
        self.assertEqual(state["unknown_active_calls"], 1)
        self.assertEqual(state["unknown_recent_calls"], 1)
        self.assertFalse(state["coverage_complete"])
        tracker.finish(unknown)
        self.ready({"ok": False, "error": {"code": "runtime_unavailable", "message": "absent"}})
        self.assert_unavailable(self.snapshot()["activity"], "unavailable")

    def test_capacity_limits_and_detail_truncation_are_preserved(self):
        tracker = ActivityTracker(max_sessions=1)
        first = tracker.begin(SimpleNamespace(request_context=SimpleNamespace(
            meta={"openai/session": "fiction-a"})), "read_file", "frontend")
        overflow = tracker.begin(SimpleNamespace(request_context=SimpleNamespace(
            meta={"openai/session": "fiction-b"})), "read_file", "frontend")
        self.ready({"ok": True, **tracker.snapshot()})
        state = self.snapshot()["activity"]
        self.assertEqual(state["active_calls"], 2)
        self.assertEqual(state["active_sessions"], 1)
        self.assertTrue(state["capacity_limited"])
        self.assertFalse(state["coverage_complete"])
        tracker.finish(first)
        tracker.finish(overflow)


if __name__ == "__main__":
    unittest.main()
