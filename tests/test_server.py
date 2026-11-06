import base64
import http.client
import io
import json
import os
import runpy
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import server

ROOT = Path(__file__).resolve().parent.parent


class EnvFileTests(unittest.TestCase):
    def test_reads_settings_without_overriding_real_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".env.local").write_text(
                "# comment\n\nnot a setting\nNEW_ONE = hello \nQUOTED=\"q\"\nSINGLE='s'\nKEEP=from-file\n",
                encoding="utf-8")
            with mock.patch.object(server, "ROOT", Path(tmp)), \
                    mock.patch.dict(os.environ, {"KEEP": "from-env"}, clear=False):
                server.load_env_file()
                self.assertEqual(os.environ["NEW_ONE"], "hello")
                self.assertEqual(os.environ["QUOTED"], "q")
                self.assertEqual(os.environ["SINGLE"], "s")
                self.assertEqual(os.environ["KEEP"], "from-env")
            for k in ("NEW_ONE", "QUOTED", "SINGLE"):
                os.environ.pop(k, None)

    def test_missing_file_is_fine(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(server, "ROOT", Path(tmp)):
            server.load_env_file()

    def test_log(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            server.log("hello")
        self.assertRegex(out.getvalue(), r"^\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] hello\n$")


def done(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


class RefresherTests(unittest.TestCase):
    def setUp(self):
        self.r = server.Refresher()
        p = mock.patch("sys.stdout", new_callable=io.StringIO)
        p.start()
        self.addCleanup(p.stop)

    def test_success(self):
        with mock.patch("subprocess.run", return_value=done()) as run:
            self.r._run(False)
        self.assertIsNotNone(self.r.last_ok)
        self.assertIsNone(self.r.last_error)
        self.assertFalse(self.r.running)
        cmds = [c.args[0] for c in run.call_args_list]
        self.assertEqual([c[1] for c in cmds], ["pull.py", "build.py"])
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["DASHBOARD_OUT"], str(server.OUT))

    def test_full_history(self):
        with mock.patch("subprocess.run", return_value=done()) as run:
            self.r._run(True)
        self.assertEqual(run.call_args_list[0].args[0][-1], "--full")

    def test_failure_reports_the_reason(self):
        out = "progress\r  bar\n\n\nStopped: GARMIN_PERSONAL_API_KEY is not set.\nRun it again any time.\n"
        with mock.patch("subprocess.run", return_value=done(1, out)):
            self.r._run(False)
        self.assertEqual(self.r.last_error, "Stopped: GARMIN_PERSONAL_API_KEY is not set.")
        self.assertIsNone(self.r.last_ok)

    def test_failure_without_a_reason_reports_last_line(self):
        with mock.patch("subprocess.run", return_value=done(1, "progress\r  bar\n", "Traceback...\nValueError: boom\n")):
            self.r._run(False)
        self.assertEqual(self.r.last_error, "ValueError: boom")

    def test_failure_without_output(self):
        with mock.patch("subprocess.run", return_value=done(2)):
            self.r._run(False)
        self.assertEqual(self.r.last_error, "pull.py failed")

    def test_crash_is_reported(self):
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pull.py", 1800)):
            self.r._run(False)
        self.assertIn("timed out", self.r.last_error)
        self.assertFalse(self.r.running)

    def test_only_one_refresh_at_a_time(self):
        release = threading.Event()

        def slow(*a, **k):
            release.wait(5)
            return done()
        with mock.patch("subprocess.run", side_effect=slow):
            self.assertTrue(self.r.start())
            self.assertFalse(self.r.start())
            self.assertTrue(self.r.status()["running"])
            release.set()
            for _ in range(200):
                if not self.r.running:
                    break
                threading.Event().wait(0.01)
        self.assertFalse(self.r.status()["running"])
        self.assertIsNotNone(self.r.status()["lastOk"])


class SchedulerTests(unittest.TestCase):
    def test_no_auto_refresh_with_existing_dashboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "dashboard.html")
            out.write_text("x")
            with mock.patch.object(server, "OUT", out), mock.patch.object(server, "REFRESH_MINUTES", 0), \
                    mock.patch.object(server.refresher, "start") as start:
                server.scheduler()
            start.assert_not_called()

    def test_first_build_even_without_auto_refresh(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(server, "OUT", Path(tmp, "none.html")), \
                mock.patch.object(server, "REFRESH_MINUTES", 0), mock.patch.object(server.refresher, "start") as start:
            server.scheduler()
        start.assert_called_once()

    def test_refreshes_on_a_timer(self):
        class Stop(Exception):
            pass
        with mock.patch.object(server, "REFRESH_MINUTES", 30), \
                mock.patch.object(server.refresher, "start") as start, \
                mock.patch("time.sleep", side_effect=[None, Stop()]) as sleep:
            with self.assertRaises(Stop):
                server.scheduler()
        self.assertEqual(start.call_count, 2)          # at start-up, then after the first wait
        sleep.assert_called_with(30 * 60)


class HandlerTests(unittest.TestCase):
    """Real HTTP requests against the handler on a random local port."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name, "dashboard.html")
        self.refresher = server.Refresher()
        for attr, value in (("OUT", self.out), ("PASSWORD", ""), ("USER", ""), ("refresher", self.refresher)):
            p = mock.patch.object(server, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def request(self, method, path, auth=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = dict(headers or {})
        if auth is not None:
            h["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
        conn.request(method, path, headers=h)
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r, body

    def test_health_check_needs_no_login(self):
        with mock.patch.object(server, "PASSWORD", "pw"):
            r, body = self.request("GET", "/healthz")
        self.assertEqual((r.status, body), (200, b"ok"))

    def test_dashboard(self):
        self.out.write_text("<h1>dash</h1>", encoding="utf-8")
        for path in ("/", "/index.html", "/dashboard.html", "/?x=1"):
            r, body = self.request("GET", path)
            self.assertEqual(r.status, 200, path)
            self.assertEqual(body, b"<h1>dash</h1>")
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")

    def test_waiting_page_before_first_build(self):
        self.refresher.last_error = "<b>Garmin refused the key</b>"
        r, body = self.request("GET", "/")
        self.assertEqual(r.status, 200)
        self.assertIn(b"Getting your Garmin data", body)
        self.assertIn(b"&lt;b>Garmin refused", body)
        self.assertNotIn(b"<b>Garmin", body)

    def test_status(self):
        r, body = self.request("GET", "/status")
        self.assertEqual(r.getheader("Content-Type"), "application/json")
        s = json.loads(body)
        self.assertEqual(set(s), {"running", "lastOk", "lastRun", "lastError", "hasDashboard", "autoRefreshMinutes"})

    def test_not_found(self):
        self.assertEqual(self.request("GET", "/nope")[0].status, 404)
        self.assertEqual(self.request("POST", "/nope")[0].status, 404)

    def test_head(self):
        self.out.write_text("abc", encoding="utf-8")
        r, body = self.request("HEAD", "/")
        self.assertEqual((r.status, body, r.getheader("Content-Length")), (200, b"", "3"))

    def test_refresh(self):
        with mock.patch.object(self.refresher, "start", return_value=True) as start:
            r, body = self.request("POST", "/refresh")
            self.assertEqual(r.status, 202)
            self.assertTrue(json.loads(body)["started"])
            start.assert_called_with(full=False)
            self.request("POST", "/refresh?full=1")
            start.assert_called_with(full=True)

    def test_password(self):
        self.out.write_text("secret dashboard", encoding="utf-8")
        with mock.patch.object(server, "PASSWORD", "pw-123"):
            r, body = self.request("GET", "/")
            self.assertEqual(r.status, 401)
            self.assertIn("Basic", r.getheader("WWW-Authenticate"))
            self.assertNotIn(b"secret", body)
            self.assertEqual(self.request("GET", "/", auth="me:wrong")[0].status, 401)
            self.assertEqual(self.request("GET", "/", auth="anyone:pw-123")[0].status, 200)
            self.assertEqual(self.request("GET", "/status")[0].status, 401)
            self.assertEqual(self.request("POST", "/refresh")[0].status, 401)
            # broken or foreign Authorization headers
            self.assertEqual(self.request("GET", "/", headers={"Authorization": "Basic !!!not-base64"})[0].status, 401)
            self.assertEqual(self.request("GET", "/", headers={"Authorization": "Basic " + base64.b64encode(b"\xff\xfe").decode()})[0].status, 401)
            self.assertEqual(self.request("GET", "/", headers={"Authorization": "Bearer pw-123"})[0].status, 401)
            with mock.patch.object(server, "USER", "varun"):
                self.assertEqual(self.request("GET", "/", auth="me:pw-123")[0].status, 401)
                self.assertEqual(self.request("GET", "/", auth="varun:pw-123")[0].status, 200)


class FakeHTTPServer:
    instances = []

    def __init__(self, addr, handler):
        self.addr = addr
        FakeHTTPServer.instances.append(self)

    def serve_forever(self):
        raise KeyboardInterrupt


class MainTests(unittest.TestCase):
    def run_main(self, **attrs):
        FakeHTTPServer.instances.clear()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(server, "DATA", Path(tmp, "data")), \
                mock.patch.object(server, "ThreadingHTTPServer", FakeHTTPServer), \
                mock.patch("threading.Thread") as thread, \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            with mock.patch.multiple(server, **attrs):
                server.main()
            self.assertTrue(Path(tmp, "data").is_dir())
        thread.return_value.start.assert_called_once()
        return out.getvalue(), FakeHTTPServer.instances[0].addr

    def test_start_up_messages(self):
        out, addr = self.run_main(HOST="0.0.0.0", PORT=8080, PASSWORD="", REFRESH_MINUTES=60)
        self.assertEqual(addr, ("0.0.0.0", 8080))
        self.assertIn("http://localhost:8080 (no password set)", out)
        self.assertIn("Auto refresh every 60 minutes", out)
        out, addr = self.run_main(HOST="127.0.0.1", PORT=9000, PASSWORD="x", REFRESH_MINUTES=0)
        self.assertIn("http://127.0.0.1:9000 (password protected)", out)
        self.assertIn("Auto refresh off", out)

    def test_runs_as_script(self):
        FakeHTTPServer.instances.clear()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"GARMIN_DATA_DIR": tmp, "PORT": "8123"}), \
                mock.patch("http.server.ThreadingHTTPServer", FakeHTTPServer), \
                mock.patch("threading.Thread"), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            runpy.run_path(str(ROOT / "server.py"), run_name="__main__")
        self.assertEqual(FakeHTTPServer.instances[0].addr, ("0.0.0.0", 8123))
        self.assertIn("Dashboard running", out.getvalue())


if __name__ == "__main__":
    unittest.main()
