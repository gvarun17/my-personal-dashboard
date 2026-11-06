import json
import os
import time
import unittest
import urllib.error
from unittest import mock

import garmin_api
import settings
from tests.helpers import FakeResponse, TempDataCase, http_error


class ReadKeyTests(TempDataCase):
    def test_key_from_environment_wins(self):
        garmin_api.ENV_FILE.write_text("GARMIN_PERSONAL_API_KEY=from-file\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"GARMIN_PERSONAL_API_KEY": "  from-env  "}):
            self.assertEqual(garmin_api._read_key(), "from-env")

    def test_key_from_env_file(self):
        garmin_api.ENV_FILE.write_text("# settings\nOTHER=1\nGARMIN_PERSONAL_API_KEY= from-file \n", encoding="utf-8")
        self.assertEqual(garmin_api._read_key(), "from-file")

    def test_empty_key_in_file_is_an_error(self):
        garmin_api.ENV_FILE.write_text("GARMIN_PERSONAL_API_KEY=\n", encoding="utf-8")
        with self.assertRaisesRegex(garmin_api.GarminError, "No Garmin key is set up yet"):
            garmin_api._read_key()

    def test_missing_file_is_an_error(self):
        with self.assertRaisesRegex(garmin_api.GarminError, "start page"):
            garmin_api._read_key()
        self.assertEqual(garmin_api.find_key(), (None, None))

    def test_start_page_key_wins_and_sources_are_reported(self):
        garmin_api.ENV_FILE.write_text("GARMIN_PERSONAL_API_KEY=from-file\n", encoding="utf-8")
        self.assertEqual(garmin_api.find_key(), ("from-file", ".env.local"))
        with mock.patch.dict(os.environ, {"GARMIN_PERSONAL_API_KEY": "from-env"}):
            self.assertEqual(garmin_api.find_key(), ("from-env", "environment"))
            settings.save(key=" from-page ")
            self.assertEqual(garmin_api.find_key(), ("from-page", "start page"))
            self.assertEqual(garmin_api._read_key(), "from-page")


class TokenTests(TempDataCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.dict(os.environ, {"GARMIN_PERSONAL_API_KEY": "secret-test-key"})
        p.start()
        self.addCleanup(p.stop)

    def test_new_token_is_saved(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(b'"tok-123"\n')) as op:
            self.assertEqual(garmin_api._new_token(), "tok-123")
        req = op.call_args[0][0]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Authorization"), "ApiKey secret-test-key")
        saved = json.loads(garmin_api.TOKEN_FILE.read_text())
        self.assertEqual(saved["token"], "tok-123")
        if os.name != "nt":
            self.assertEqual(garmin_api.TOKEN_FILE.stat().st_mode & 0o777, 0o600)

    def test_check_key_uses_the_given_key(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(b"tok-9")) as op:
            self.assertEqual(garmin_api.check_key("typed-on-start-page"), "tok-9")
        self.assertEqual(op.call_args[0][0].get_header("Authorization"), "ApiKey typed-on-start-page")

    def test_refused_key_never_shows_the_key(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(401)):
            with self.assertRaises(garmin_api.GarminError) as cm:
                garmin_api._new_token()
        self.assertIn("HTTP 401", str(cm.exception))
        self.assertNotIn("secret-test-key", str(cm.exception))

    def test_network_down(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            with self.assertRaisesRegex(garmin_api.GarminError, "Could not reach Garmin: offline"):
                garmin_api._new_token()

    def test_empty_token(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(b"  ")):
            with self.assertRaisesRegex(garmin_api.GarminError, "empty token"):
                garmin_api._new_token()

    def test_fresh_cached_token_is_reused(self):
        garmin_api.TOKEN_FILE.write_text(json.dumps({"token": "cached", "fetched_at": time.time()}))
        with mock.patch.object(garmin_api, "_new_token") as new:
            self.assertEqual(garmin_api.get_token(), "cached")
        new.assert_not_called()

    def test_stale_corrupt_or_empty_cache_gets_new_token(self):
        for content in (json.dumps({"token": "old", "fetched_at": time.time() - 21 * 3600}),
                        "not json", json.dumps({"fetched_at": time.time()}),
                        json.dumps({"token": "", "fetched_at": time.time()})):
            garmin_api.TOKEN_FILE.write_text(content)
            with mock.patch.object(garmin_api, "_new_token", return_value="fresh"):
                self.assertEqual(garmin_api.get_token(), "fresh")

    def test_no_cache_and_force(self):
        with mock.patch.object(garmin_api, "_new_token", return_value="fresh"):
            self.assertEqual(garmin_api.get_token(), "fresh")
        garmin_api.TOKEN_FILE.write_text(json.dumps({"token": "cached", "fetched_at": time.time()}))
        with mock.patch.object(garmin_api, "_new_token", return_value="forced"):
            self.assertEqual(garmin_api.get_token(force=True), "forced")


class GetTests(TempDataCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(garmin_api, "get_token", return_value="tok")
        self.get_token = p.start()
        self.addCleanup(p.stop)

    def test_returns_json_with_bearer_token(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(b'{"a": 1}')) as op:
            self.assertEqual(garmin_api.get("/x"), {"a": 1})
        req = op.call_args[0][0]
        self.assertEqual(req.full_url, garmin_api.BASE + "/x")
        self.assertEqual(req.get_header("Authorization"), "Bearer tok")

    def test_empty_body_is_none(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(b" \n")):
            self.assertIsNone(garmin_api.get("/x"))

    def test_401_renews_token_once(self):
        with mock.patch("urllib.request.urlopen", side_effect=[http_error(401), FakeResponse(b"[1]")]):
            self.assertEqual(garmin_api.get("/x"), [1])
        self.get_token.assert_called_with(force=True)

    def test_second_401_is_raised(self):
        with mock.patch("urllib.request.urlopen", side_effect=[http_error(401), http_error(401)]):
            with self.assertRaises(garmin_api.HTTPStatus) as cm:
                garmin_api.get("/x")
        self.assertEqual(cm.exception.code, 401)

    def test_busy_server_is_retried(self):
        for code in (429, 502, 503):
            with mock.patch("urllib.request.urlopen", side_effect=[http_error(code), FakeResponse(b"{}")]):
                self.assertEqual(garmin_api.get("/x"), {})

    def test_busy_server_gives_up(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(429)):
            with self.assertRaises(garmin_api.HTTPStatus) as cm:
                garmin_api.get("/x", retries=2)
        self.assertEqual(cm.exception.code, 429)

    def test_500_is_raised_immediately(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(500)) as op:
            with self.assertRaises(garmin_api.HTTPStatus):
                garmin_api.get("/x")
        self.assertEqual(op.call_count, 1)

    def test_network_errors_are_retried_then_raised(self):
        with mock.patch("urllib.request.urlopen", side_effect=[urllib.error.URLError("x"), FakeResponse(b"1")]):
            self.assertEqual(garmin_api.get("/x"), 1)
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            with self.assertRaisesRegex(garmin_api.GarminError, "Could not reach Garmin: down"):
                garmin_api.get("/x", retries=1)

    def test_gives_up_when_retries_run_out(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(401)):
            with self.assertRaisesRegex(garmin_api.GarminError, "Gave up"):
                garmin_api.get("/x", retries=0)


if __name__ == "__main__":
    unittest.main()
