"""MCP stdio test: spawn the real server, walk analyze->get_result->export (mock).

Needs app/.venv with requirements-mcp.txt (skipped otherwise). Creates one
mock run under runs/mcp-* and removes it afterwards. No network, no spending.
"""
import json
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

from cli import APP_DIR

VENV_PY = APP_DIR / ".venv" / "bin" / "python"


class McpStdioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not VENV_PY.is_file():
            raise unittest.SkipTest("app/.venv missing (pip install -r requirements-mcp.txt)")
        probe = subprocess.run([str(VENV_PY), "-c", "import mcp"],
                                 capture_output=True)
        if probe.returncode != 0:
            raise unittest.SkipTest("mcp SDK missing from app/.venv")
        cls.before = {p.name for p in (APP_DIR / "runs").iterdir()} if (APP_DIR / "runs").is_dir() else set()

    @classmethod
    def tearDownClass(cls):
        runs = APP_DIR / "runs"
        if runs.is_dir():
            for p in runs.iterdir():
                if p.name not in cls.before and p.name.startswith("mcp-"):
                    shutil.rmtree(p, ignore_errors=True)

    def _session(self):
        proc = subprocess.Popen(
            [str(VENV_PY), "-B", str(APP_DIR / "mcp_server.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        seq = {"n": 0}

        def call(method, params=None, timeout=420):
            seq["n"] += 1
            rid = seq["n"]
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                         "method": method, "params": params or {}}) + "\n")
            proc.stdin.flush()
            deadline = time.time() + timeout
            while time.time() < deadline:
                line = proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("id") == rid:
                    if "error" in msg:
                        raise AssertionError(f"{method} RPC error: {msg['error']}")
                    return msg["result"]
            raise AssertionError(f"{method} timed out")

        def notify(method):
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
            proc.stdin.flush()

        return proc, call, notify

    def test_walk_analyze_get_export(self):
        _, call, notify = self._session()
        init = call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                   "clientInfo": {"name": "t", "version": "0"}})
        self.assertIn("serverInfo", init)
        notify("notifications/initialized")
        tools = call("tools/list")
        names = {t["name"] for t in tools["tools"]}
        self.assertEqual(names, {"list_examples", "analyze_video", "get_result",
                                 "export", "list_runs", "send_to_navide"})
        ex = call("tools/call", {"name": "list_examples", "arguments": {}})
        self.assertIn("synthetic", ex["content"][0]["text"])
        schema = next(t for t in tools["tools"] if t["name"] == "analyze_video")
        self.assertEqual(schema["inputSchema"]["properties"]["model"]["enum"],
                         ["muse-spark-1.2", "muse-spark-1.3"])
        self.assertIn("task_hint", schema["inputSchema"]["properties"])
        self.assertEqual(schema["inputSchema"]["properties"]["keyframes"]["default"], True)
        bad_model = call("tools/call", {"name": "analyze_video",
                                       "arguments": {"example": "synthetic", "model": "gpt-9"}})
        self.assertTrue(bad_model.get("isError"))
        mismatch = call("tools/call", {"name": "analyze_video",
                                      "arguments": {"example": "m1", "provider": "mock"}})
        self.assertTrue(mismatch.get("isError"))
        if "example file missing" in mismatch["content"][0]["text"]:
            self.skipTest("M1 clip not present on this machine")
        self.assertIn("fixture does not match", mismatch["content"][0]["text"])
        an = call("tools/call", {"name": "analyze_video",
                                 "arguments": {"example": "synthetic", "provider": "mock"}})
        self.assertFalse(an.get("isError"), an["content"][0]["text"][:300])
        run_id = json.loads(an["content"][0]["text"])["run_id"]
        gr = call("tools/call", {"name": "get_result", "arguments": {"run_id": run_id}})
        body = json.loads(gr["content"][0]["text"])
        self.assertEqual(body["state"], "done")
        self.assertEqual(body["steps"], 2)
        self.assertIn("MOCK", body["manual"])
        xp = call("tools/call", {"name": "export",
                                 "arguments": {"run_id": run_id, "format": "vla"}})
        self.assertFalse(xp.get("isError"))
        self.assertIn("subtask", xp["content"][0]["text"])
        bad = call("tools/call", {"name": "get_result", "arguments": {"run_id": "nope"}})
        self.assertTrue(bad.get("isError"))
        lr = call("tools/call", {"name": "list_runs", "arguments": {}})
        self.assertIn(run_id, lr["content"][0]["text"])
        nv = call("tools/call", {"name": "send_to_navide", "arguments": {"run_id": run_id}})
        self.assertIn("not live-connected", nv["content"][0]["text"])

    def test_meta_needs_confirm_and_key(self):
        _, call, notify = self._session()
        call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                            "clientInfo": {"name": "t", "version": "0"}})
        notify("notifications/initialized")
        r = call("tools/call", {"name": "analyze_video",
                                "arguments": {"example": "synthetic", "provider": "meta"}})
        self.assertTrue(r.get("isError"))
        self.assertIn("confirm_paid", r["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
