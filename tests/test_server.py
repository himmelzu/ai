import base64
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from backend import AppServer
from test_renderer import trip


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.server = AppServer()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, route, payload, token=True, origin=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Session-Token"] = self.server.session_token
        if origin:
            headers["Origin"] = origin
        req = Request(self.server.url + route, json.dumps(payload).encode(), headers)
        with urlopen(req, timeout=10) as response:
            return json.load(response)

    def test_token_and_origin_enforced(self):
        for token, origin in ((False, None), (True, "https://example.com")):
            with self.assertRaises(HTTPError) as exc:
                self.request("/api/recalculate", {"trips": [trip()]}, token, origin)
            self.assertEqual(exc.exception.code, 403)

    def test_calculate_render_roundtrip(self):
        result = self.request("/api/recalculate", {"trips": [trip()]})
        self.assertEqual(result["trips"][0]["daily_allowance"], 20000)
        pdf = self.request("/api/render", dict(trips=result["trips"], rates_confirmed=True, data_confirmed=True))
        self.assertFalse(pdf["is_draft"])
        self.assertTrue(base64.b64decode(pdf["data"]).startswith(b"%PDF-"))

    def test_oversize_and_malformed_images_rejected(self):
        with self.assertRaises(HTTPError) as exc:
            self.request("/api/parse", {"filename": "bad.png", "data": "%%%"})
        self.assertEqual(exc.exception.code, 400)

    def test_unexpected_render_error_is_diagnosable_without_private_data(self):
        private = "PRIVATE_EMPLOYEE_ABC C:/Users/private-user/claim.pdf session-secret"
        log = io.StringIO()
        with redirect_stderr(log), patch("backend.render_pdf", side_effect=RuntimeError(private)):
            with self.assertRaises(HTTPError) as error:
                self.request("/api/render", {"trips": [trip()], "source_name": private})
            self.assertEqual(error.exception.code, 500)
            result = json.load(error.exception)
        self.assertEqual(result["error_code"], "PDF_RENDER_FAILED")
        self.assertIn("RuntimeError", result["diagnostics"])
        self.assertIn("backend.py:", result["diagnostics"])
        self.assertNotIn("다른 캡처", result["error"])
        for output in (json.dumps(result), log.getvalue()):
            self.assertNotIn("PRIVATE_EMPLOYEE", output)
            self.assertNotIn("private-user", output)
            self.assertNotIn("session-secret", output)
            self.assertNotIn(self.server.session_token, output)
        # A failed request must leave the running application usable for retry.
        retry = self.request("/api/render", {"trips": [trip()]})
        self.assertTrue(base64.b64decode(retry["data"]).startswith(b"%PDF-"))

    def test_unexpected_input_error_has_input_stage_code(self):
        with redirect_stderr(io.StringIO()), patch("backend.parse_capture", side_effect=OSError("private path")):
            with self.assertRaises(HTTPError) as error:
                self.request("/api/parse", {"filename": "capture.png", "data": base64.b64encode(b"fixture").decode()})
            result = json.load(error.exception)
        self.assertEqual(result["error_code"], "INPUT_PARSE_FAILED")
        self.assertNotIn("private path", result["diagnostics"])


if __name__ == "__main__":
    unittest.main()
