import base64
from contextlib import redirect_stderr
import io
import json
import re
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
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
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-Session-Token'] = self.server.session_token
        if origin:
            headers['Origin'] = origin
        req = Request(self.server.url + route, json.dumps(payload).encode(), headers)
        with urlopen(req, timeout=30) as response:
            return json.load(response)

    def test_token_and_origin_enforced(self):
        for route in ('/api/recalculate', '/api/parse', '/api/render'):
            for token, origin in ((False, None), (True, 'https://example.com')):
                with self.assertRaises(HTTPError) as exc:
                    self.request(route, {'trips': [trip()]}, token, origin)
                self.assertEqual(exc.exception.code, 403)

    def test_render_without_confirmation_flags_has_clean_filename_and_output(self):
        calculated = self.request('/api/recalculate', {'trips': [trip()]})
        self.assertEqual(calculated['trips'][0]['daily_allowance'], 20000)
        result = self.request('/api/render', {'trips': calculated['trips']})
        self.assertFalse(result['needs_review'])
        self.assertEqual(result['filename'], '여비지급명세서.pdf')
        with fitz.open(stream=base64.b64decode(result['data']), filetype='pdf') as document:
            text = ''.join(page.get_text() for page in document)
            self.assertIn('20,000', text)
            self.assertNotIn('검토용', text)
            self.assertNotIn('검토용', json.dumps(document.metadata, ensure_ascii=False))

    def test_missing_values_stay_unknown_and_warnings_only_appear_on_screen(self):
        incomplete = trip(meal=None)
        result = self.request('/api/render', {'trips': [incomplete]})
        self.assertTrue(result['needs_review'])
        self.assertTrue(result['warnings'])
        self.assertEqual(result['filename'], '여비지급명세서.pdf')
        with fitz.open(stream=base64.b64decode(result['data']), filetype='pdf') as document:
            text = ''.join(page.get_text() for page in document)
            self.assertIn('확인필요', re.sub(r'\s+', '', text))
            self.assertNotIn('검토용', text)
            self.assertNotIn('검토 중', text)

    def test_pdf_only_and_removed_layout_route(self):
        for payload in ({'filename': 'bad.pdf', 'data': '%%%'},
                        {'filename': 'image.png', 'data': base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode()}):
            with self.assertRaises(HTTPError) as error:
                self.request('/api/parse', payload)
            self.assertEqual(error.exception.code, 400)
            response = json.load(error.exception)
            self.assertEqual(response['error_code'], 'INPUT_VALIDATION_FAILED')
        with self.assertRaises(HTTPError) as error:
            self.request('/api/layout', {})
        self.assertEqual(error.exception.code, 404)

    def test_unexpected_error_diagnostics_exclude_private_values_and_server_can_retry(self):
        private = 'PRIVATE_EMPLOYEE C:/Users/private-user/claim.pdf session-secret'
        log = io.StringIO()
        with redirect_stderr(log), patch('backend.render_pdf', side_effect=RuntimeError(private)):
            with self.assertRaises(HTTPError) as error:
                self.request('/api/render', {'trips': [trip()], 'source_name': private})
            self.assertEqual(error.exception.code, 500)
            result = json.load(error.exception)
        self.assertEqual(result['error_code'], 'PDF_RENDER_FAILED')
        self.assertIn('RuntimeError', result['diagnostics'])
        self.assertIn('backend.py:', result['diagnostics'])
        for output in (json.dumps(result), log.getvalue()):
            for value in ('PRIVATE_EMPLOYEE', 'private-user', 'session-secret', self.server.session_token):
                self.assertNotIn(value, output)
        retry = self.request('/api/render', {'trips': [trip()]})
        self.assertTrue(base64.b64decode(retry['data']).startswith(b'%PDF-'))

    def test_pdf_parse_error_identifies_stage_without_private_path(self):
        with redirect_stderr(io.StringIO()), patch('backend.parse_pdf_input', side_effect=OSError('private path')):
            with self.assertRaises(HTTPError) as error:
                self.request('/api/parse', {'filename': 'report.pdf', 'data': base64.b64encode(b'%PDF-fixture').decode()})
            result = json.load(error.exception)
        self.assertEqual(result['error_code'], 'INPUT_PARSE_FAILED')
        self.assertNotIn('private path', result['diagnostics'])


if __name__ == '__main__':
    unittest.main()
