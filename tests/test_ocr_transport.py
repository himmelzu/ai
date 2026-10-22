"""OCR must work without writing an image beneath a Windows user/TEMP path."""
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import ocr_parser as ocr


class OcrInputTransportTests(unittest.TestCase):
    def test_ocr_works_without_a_writable_temporary_directory(self):
        if not shutil.which("tesseract"):
            self.skipTest("Tesseract is not installed")
        with fitz.open() as document:
            page = document.new_page(width=150, height=40)
            page.insert_text((5, 28), "10:57", fontsize=25)
            capture = page.get_pixmap(alpha=False)
        # Windows users can have Korean names or a restricted TEMP directory.
        # The old temporary-PNG input path failed before the engine could read it.
        with patch("tempfile.TemporaryDirectory", side_effect=PermissionError("fixture-only restricted TEMP")):
            words = ocr._run_ocr(capture, 2, numeric=True)
        self.assertEqual(ocr._times(ocr._cell_text(words)), ["10:57"])


if __name__ == "__main__":
    unittest.main()
