import base64
import http.client
import io
import json
import os
import runpy
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode
from pathlib import Path
from unittest import mock

import garmin_api
import server
import settings


class Isolated(unittest.TestCase):
    """Settings and .env.local point at a temporary folder; a key is set up."""

    def setUp(self):
        self._iso = tempfile.TemporaryDirectory()
        self.addCleanup(self._iso.cleanup)
        self.iso = Path(self._iso.name)
        for target, attr, value in ((settings, "DATA", self.iso), (garmin_api, "ENV_FILE", self.iso / ".env.local")):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        if hasattr(time, "tzset"):
            self.addCleanup(time.tzset)        # runs last: re-read the restored TZ
        p = mock.patch.dict(os.environ, {"GARMIN_PERSONAL_API_KEY": ""})
        p.start()
        self.addCleanup(p.stop)
        settings.save(name="Tester", tz="Europe/London", key="test-key")

    def clear_key(self):
        settings.path().unlink()

SRC = Path(__file__).resolve().parent.parent / "src"


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


class SetupHelperTests(Isolated):
    def test_child_env_uses_current_settings(self):
        with mock.patch.dict(os.environ, {"TZ": "Asia/Tokyo"}):
            env = server.child_env()
        self.assertEqual(env["TZ"], "Asia/Tokyo")
        self.assertEqual(env["GARMIN_DATA_DIR"], str(server.DATA))
        self.assertEqual(env["DASHBOARD_OUT"], str(server.OUT))

    def test_configured(self):
        self.assertTrue(server.configured())
        self.clear_key()
        self.assertFalse(server.configured())

    def test_apply_tz(self):
        with mock.patch.dict(os.environ, {"TZ": "UTC"}):
            with mock.patch.object(server.time, "tzset", create=True) as tzset:
                server.apply_tz("Asia/Kolkata")
                self.assertEqual(os.environ["TZ"], "Asia/Kolkata")
                tzset.assert_called_once()
                server.apply_tz(None)
                server.apply_tz("")
                self.assertEqual(os.environ["TZ"], "Asia/Kolkata")
            with mock.patch("server.hasattr", return_value=False, create=True):   # Windows: no tzset
                server.apply_tz("Europe/Paris")
            self.assertEqual(os.environ["TZ"], "Europe/Paris")
        if hasattr(server.time, "tzset"):
            server.time.tzset()

    def test_setup_page_first_time(self):
        self.clear_key()
        with mock.patch.dict(os.environ, {"TZ": "UTC"}):
            page = server.setup_page()
        self.assertIn("Welcome! Let's set up your dashboard", page)
        self.assertIn('value=""', page)                 # UTC default left for the browser to fill in
        self.assertIn("required placeholder=\"Paste your key here\"", page)
        self.assertIn("Connect to Garmin", page)
        self.assertNotIn("Back to the dashboard", page)
        self.assertIn("personalApiToken", page)

    def test_setup_page_when_set_up(self):
        page = server.setup_page(error="<script>bad</script>")
        self.assertIn("Settings", page)
        self.assertIn('value="Tester"', page)
        self.assertIn('value="Europe/London"', page)
        self.assertIn("A key is set up (from the start page)", page)
        self.assertIn("Leave empty to keep your current key", page)
        self.assertIn("&lt;script&gt;bad", page)
        self.assertNotIn("<script>bad", page)
        self.assertNotIn("test-key", page)              # the key is never sent back
        for z in settings.time_zones()[:3]:
            self.assertIn(f'<option value="{z}">', page)

    def test_setup_page_keeps_typed_values(self):
        page = server.setup_page(name='A "quoted" name', tz="Asia/Tokyo")
        self.assertIn('value="A &quot;quoted&quot; name"', page)
        self.assertIn('value="Asia/Tokyo"', page)


class SchedulerTests(Isolated):
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

    def test_waits_for_setup_before_refreshing(self):
        self.clear_key()

        class Stop(Exception):
            pass
        with mock.patch.object(server, "REFRESH_MINUTES", 30), \
                mock.patch.object(server.refresher, "start") as start, \
                mock.patch("time.sleep", side_effect=[None, Stop()]):
            with self.assertRaises(Stop):
                server.scheduler()
        start.assert_not_called()


class HandlerTests(Isolated):
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
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name, "dashboard.html")
        self.refresher = server.Refresher()
        for attr, value in (("OUT", self.out), ("PASSWORD", ""), ("USER", ""), ("refresher", self.refresher)):
            p = mock.patch.object(server, attr, value)
            p.start()
            self.addCleanup(p.stop)

    def request(self, method, path, auth=None, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = dict(headers or {})
        if auth is not None:
            h["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
        if isinstance(body, dict):
            body = urlencode(body)
            h.setdefault("Content-Type", "application/x-www-form-urlencoded")
        conn.request(method, path, body=body, headers=h)
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


class SetupRouteTests(HandlerTests):
    """The start page over real HTTP."""

    def setUp(self):
        super().setUp()
        for attr in ("apply_tz",):
            p = mock.patch.object(server, attr)
            setattr(self, attr, p.start())
            self.addCleanup(p.stop)
        p = mock.patch.object(self.refresher, "start", return_value=True)
        self.refresh_start = p.start()
        self.addCleanup(p.stop)
        p = mock.patch("sys.stdout", new_callable=io.StringIO)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(settings, "time_zones", return_value=["Asia/Kolkata", "Europe/London"])
        p.start()
        self.addCleanup(p.stop)

    def post(self, form, headers=None):
        return self.request("POST", "/setup", body=form, headers=headers)

    def test_first_visit_goes_to_the_start_page(self):
        self.clear_key()
        r, _ = self.request("GET", "/")
        self.assertEqual((r.status, r.getheader("Location")), (303, "setup"))
        for path in ("/setup", "/setup/"):
            r, body = self.request("GET", path)
            self.assertEqual(r.status, 200)
            self.assertIn(b"Welcome!", body)

    def test_older_browser_time_zone_name_is_accepted(self):
        self.clear_key()
        with mock.patch.object(garmin_api, "check_key"):
            r, _ = self.post({"name": "Varun", "tz": "Asia/Calcutta", "key": "k"})
        self.assertEqual(r.status, 303)
        self.assertEqual(settings.load()["tz"], "Asia/Kolkata")

    def test_first_time_setup(self):
        self.clear_key()
        with mock.patch.object(garmin_api, "check_key", return_value="tok") as check:
            r, _ = self.post({"name": "  Varun ", "tz": "Asia/Kolkata", "key": " abc 123\n"})
        self.assertEqual((r.status, r.getheader("Location")), (303, "./"))
        check.assert_called_once_with("abc123")
        saved = settings.load()
        self.assertEqual((saved["name"], saved["tz"], saved["key"]), ("Varun", "Asia/Kolkata", "abc123"))
        self.apply_tz.assert_called_once_with("Asia/Kolkata")
        self.refresh_start.assert_called_once()
        self.assertTrue(server.configured())

    def test_changing_name_keeps_the_key(self):
        with mock.patch.object(garmin_api, "check_key") as check:
            r, _ = self.post({"name": "New Name", "tz": "Europe/London", "key": ""})
        self.assertEqual(r.status, 303)
        check.assert_not_called()
        self.assertEqual(settings.load()["key"], "test-key")
        self.assertEqual(settings.load()["name"], "New Name")

    def test_mistakes_are_shown_and_nothing_is_saved(self):
        self.clear_key()
        cases = [({"name": " ", "tz": "Asia/Kolkata", "key": "k"}, b"Please enter your name"),
                 ({"name": "V", "tz": "Mars/Base", "key": "k"}, b"pick your time zone"),
                 ({"name": "V", "tz": "Asia/Kolkata", "key": ""}, b"paste your Garmin Personal API key"),
                 ({"name": "V"}, b"pick your time zone")]
        for form, message in cases:
            r, body = self.post(form)
            self.assertEqual(r.status, 400, form)
            self.assertIn(message, body)
        self.assertFalse(settings.path().exists())
        self.refresh_start.assert_not_called()

    def test_refused_key(self):
        self.clear_key()
        refused = garmin_api.GarminError("Garmin refused the key (HTTP 401).")
        with mock.patch.object(garmin_api, "check_key", side_effect=refused):
            r, body = self.post({"name": "Varun", "tz": "Asia/Kolkata", "key": "wrong-key-value"})
        self.assertEqual(r.status, 400)
        self.assertIn(b"Garmin refused the key (HTTP 401).", body)
        self.assertIn(b'value="Varun"', body)
        self.assertNotIn(b"wrong-key-value", body)        # never echoed back
        self.assertFalse(settings.path().exists())

    def test_size_limits(self):
        r, _ = self.request("POST", "/setup", body="x" * 20001,
                            headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(r.status, 413)
        r, _ = self.request("POST", "/setup", headers={"Content-Length": "nope"})
        self.assertEqual(r.status, 413)
        r, _ = self.request("POST", "/setup")              # empty form
        self.assertEqual(r.status, 400)

    def test_other_websites_cannot_post(self):
        form = {"name": "x", "tz": "Asia/Kolkata", "key": "evil"}
        host = f"127.0.0.1:{self.port}"
        r, _ = self.post(form, {"Origin": "https://evil.example"})
        self.assertEqual(r.status, 403)
        r, _ = self.post(form, {"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status, 403)
        r, _ = self.request("POST", "/refresh", headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status, 403)
        with mock.patch.object(garmin_api, "check_key"):
            r, _ = self.post(form, {"Origin": f"http://{host}", "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(r.status, 303)

    def test_start_page_needs_the_password_too(self):
        with mock.patch.object(server, "PASSWORD", "pw"):
            self.assertEqual(self.request("GET", "/setup")[0].status, 401)
            self.assertEqual(self.post({"name": "x"})[0].status, 401)
            self.assertEqual(self.request("GET", "/setup", auth="me:pw")[0].status, 200)


class FakeHTTPServer:
    instances = []

    def __init__(self, addr, handler):
        self.addr = addr
        FakeHTTPServer.instances.append(self)

    def serve_forever(self):
        raise KeyboardInterrupt


class MainTests(Isolated):
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
        self.assertNotIn("to set up your name", out)

    def test_start_up_before_setup(self):
        self.clear_key()
        out, _ = self.run_main(HOST="0.0.0.0", PORT=8080, PASSWORD="", REFRESH_MINUTES=60)
        self.assertIn("Open http://localhost:8080 to set up your name and Garmin key", out)

    def test_saved_time_zone_is_used(self):
        with mock.patch.object(server, "apply_tz") as tz:
            self.run_main(HOST="0.0.0.0", PORT=8080, PASSWORD="", REFRESH_MINUTES=60)
        tz.assert_called_once_with("Europe/London")

    def test_runs_as_script(self):
        FakeHTTPServer.instances.clear()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"GARMIN_DATA_DIR": tmp, "PORT": "8123"}), \
                mock.patch("http.server.ThreadingHTTPServer", FakeHTTPServer), \
                mock.patch("threading.Thread"), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            runpy.run_path(str(SRC / "server.py"), run_name="__main__")
        self.assertEqual(FakeHTTPServer.instances[0].addr, ("0.0.0.0", 8123))
        self.assertIn("Dashboard running", out.getvalue())


if __name__ == "__main__":
    unittest.main()
