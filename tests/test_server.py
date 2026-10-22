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
        for route in ("/api/recalculate", "/api/layout", "/api/parse"):
            for token, origin in ((False, None), (True, "https://example.com")):
                with self.assertRaises(HTTPError) as exc:
                    self.request(route, {"trips": [trip()]}, token, origin)
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

    def test_layout_validation_error_explains_stage_without_ocr_values(self):
        error = ValueError("표 배치를 원본과 확인해 주세요.")
        error.error_code = "LAYOUT_UNRESOLVED"
        error.diagnostics = {"grid_columns": 16, "candidate_count": 0,
                             "recognized_name": "PRIVATE_NAME", "source_path": "C:/Users/PRIVATE_USER",
                             "rows": {"valid": 10, "raw_text": "PRIVATE_NAME"}}
        with patch("backend.parse_capture", side_effect=error):
            with self.assertRaises(HTTPError) as response:
                self.request("/api/parse", {"data": base64.b64encode(b"fixture").decode(), "filename": "PRIVATE_USER.png"})
        self.assertEqual(response.exception.code, 400)
        result = json.load(response.exception)
        self.assertEqual(result["error_code"], "LAYOUT_UNRESOLVED")
        self.assertIn('"candidate_count": 0', result["diagnostics"])
        self.assertIn("stage=parse", result["diagnostics"])
        self.assertNotIn("PRIVATE_", result["diagnostics"])

    def test_layout_hint_is_provisional_and_cannot_bypass_confirmation(self):
        from test_ocr_header import controlled_capture
        payload = {"filename": "pseudonym.png", "data": base64.b64encode(controlled_capture().tobytes("png")).decode()}
        hint = self.request("/api/layout", payload)["layout"]
        self.assertTrue(hint["provisional"])
        self.assertEqual(len(hint["xs"]), 13)
        self.assertEqual(hint["image_width"], 1244)
        self.assertIsNot(hint.get("confirmed"), True)
        with self.assertRaises(HTTPError) as response:
            self.request("/api/parse", {**payload, "layout": hint})
        self.assertEqual(response.exception.code, 400)

    def test_pdf_cannot_use_image_layout_route(self):
        payload = {"filename": "report.pdf", "data": base64.b64encode(b"%PDF-1.4").decode()}
        for route, extra in (("/api/layout", {}), ("/api/parse", {"layout": {"confirmed": True}})):
            with self.assertRaises(HTTPError) as response:
                self.request(route, {**payload, **extra})
            self.assertEqual(response.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
