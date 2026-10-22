"""Explicitly confirmed geometry can recover a screenshot without usable rules."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import ocr_parser as ocr
from test_ocr_parser import synthetic_capture
from test_ocr_header import controlled_capture, data_row, words_for_rows


def gridless_capture():
    pix = fitz.Pixmap(synthetic_capture())
    xs = [0, 40, 150, 240, 330, 425, 575, 820, 945, 1135, 1245, 1355, 1500]
    for x in xs:
        pix.set_rect(fitz.IRect(max(0, x - 2), 0, min(pix.width, x + 3), pix.height), (255, 255, 255))
    for y in [1, 45, 105, 174]:
        pix.set_rect(fitz.IRect(0, max(0, y - 2), pix.width, min(pix.height, y + 3)), (255, 255, 255))
    layout = {"schema": "local-trip-ui-v1", "confirmed": True,
              "xs": [x / pix.width for x in xs], "body_top": 45 / pix.height,
              "body_bottom": 174 / pix.height, "row_count": 2,
              "image_width": pix.width, "image_height": pix.height}
    return pix, layout


class GuidedGeometryTests(unittest.TestCase):
    def test_suggestion_is_provisional_even_when_grid_matches(self):
        source = controlled_capture()
        proposal = ocr.suggest_image_layout(source.tobytes("png"))
        self.assertFalse(proposal["confirmed"])
        self.assertTrue(proposal["provisional"])
        self.assertEqual(proposal["row_count"], 10)
        self.assertEqual(len(proposal["xs"]), 13)
        self.assertAlmostEqual(proposal["body_top"] * source.height, 89, delta=1)
        self.assertEqual(proposal["image_width"], source.width)
        with self.assertRaisesRegex(ValueError, "확인란"):
            ocr.parse_image_with_layout(source.tobytes("png"), "fixture.png", proposal)

    def test_malformed_stale_and_unconfirmed_geometries_rejected_before_ocr(self):
        pix, valid = gridless_capture()
        mutations = [dict(confirmed=False), dict(confirmed="true"), dict(schema="other"),
                     dict(image_width=valid["image_width"] + 1), dict(image_height=True),
                     dict(xs=valid["xs"][:-1]), dict(xs=list(reversed(valid["xs"]))),
                     dict(xs=[float("nan")] + valid["xs"][1:]),
                     dict(xs=[10**400] + valid["xs"][1:]),
                     dict(xs=[True] + valid["xs"][1:]), dict(body_top=-0.1),
                     dict(body_bottom=float("inf")), dict(body_bottom=valid["body_top"]),
                     dict(row_count=0), dict(row_count=201), dict(row_count=2.0), dict(row_count=True)]
        for change in mutations:
            with self.subTest(change=change), patch.object(ocr, "_recognize") as recognize:
                with self.assertRaises(ValueError):
                    ocr.parse_image_with_layout(pix.tobytes("png"), "fixture.png", valid | change)
                recognize.assert_not_called()

    def test_guided_missing_time_and_empty_row_are_retained_without_guessing(self):
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 1500, 200));pix.clear_with(255)
        first = data_row(actual="")
        second = [""] * 12
        words, xs, ys = words_for_rows([first, second])
        # Use the exact mock geometry, not a source-specific preset.
        layout = {"schema": "local-trip-ui-v1", "confirmed": True,
                  "xs": [x / pix.width for x in xs], "body_top": ys[0] / pix.height,
                  "body_bottom": ys[-1] / pix.height, "row_count": 2,
                  "image_width": pix.width, "image_height": pix.height}
        with patch.object(ocr, "_recognize", return_value=words), \
             patch.object(ocr, "_refine_identity_columns", side_effect=lambda _p, w, *_a: (w, {})), \
             patch.object(ocr, "_refine_actual_times", side_effect=lambda _p, w, *_a: w):
            result = ocr.parse_image_with_layout(pix.tobytes("png"), "fixture.png", layout)
        self.assertEqual(len(result["trips"]), 2)
        self.assertEqual(result["trips"][0]["start_time"], "")
        self.assertEqual(result["trips"][0]["end_time"], "")
        self.assertEqual(result["trips"][0]["requested_start_time"], "09:30")
        self.assertEqual(result["trips"][1]["name"], "")
        self.assertEqual(result["trips"][1]["vehicle"], "unknown")
        self.assertNotIn("TEST0001", json.dumps(result))

    def test_unrelated_dates_table_does_not_become_a_travel_claim(self):
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 1500, 200));pix.clear_with(255)
        row = ["1", "상품", "분류", "물품명", "123456", "10개", "2026-10-22 ~ 2026-10-23", "주소", "판매", "제2호", "배송", "처리중"]
        words, xs, ys = words_for_rows([row, row])
        layout = {"schema": "local-trip-ui-v1", "confirmed": True,
                  "xs": [x / pix.width for x in xs], "body_top": ys[0] / pix.height,
                  "body_bottom": ys[-1] / pix.height, "row_count": 2,
                  "image_width": pix.width, "image_height": pix.height}
        with patch.object(ocr, "_recognize", return_value=words), \
             patch.object(ocr, "_refine_identity_columns", side_effect=lambda _p, w, *_a: (w, {})), \
             patch.object(ocr, "_refine_actual_times", side_effect=lambda _p, w, *_a: w):
            with self.assertRaises(ocr.LayoutRecognitionError) as caught:
                ocr.parse_image_with_layout(pix.tobytes("png"), "fixture.png", layout)
        self.assertEqual(caught.exception.error_code, "OCR_GUIDED_CONTENT_UNCONFIRMED")
        self.assertTrue(all(isinstance(value, (int, float, bool)) for value in caught.exception.diagnostics.values()))

    def test_single_confirmed_body_row_has_no_synthetic_header_requirement(self):
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 1500, 200));pix.clear_with(255)
        words, xs, ys = words_for_rows([data_row()])
        layout = {"schema": "local-trip-ui-v1", "confirmed": True,
                  "xs": [x / pix.width for x in xs], "body_top": ys[0] / pix.height,
                  "body_bottom": ys[-1] / pix.height, "row_count": 1,
                  "image_width": pix.width, "image_height": pix.height}
        with patch.object(ocr, "_recognize", return_value=words), \
             patch.object(ocr, "_refine_identity_columns", side_effect=lambda _p, w, *_a: (w, {})), \
             patch.object(ocr, "_refine_actual_times", side_effect=lambda _p, w, *_a: w):
            result = ocr.parse_image_with_layout(pix.tobytes("png"), "fixture.png", layout)
        self.assertEqual(len(result["trips"]), 1)
        self.assertEqual(result["trips"][0]["name"], "가명인")

    def test_layout_failure_diagnostics_have_no_ocr_or_private_text(self):
        words, xs, ys = words_for_rows([["private person secret path"] * 12] * 3)
        with self.assertRaises(ocr.LayoutRecognitionError) as caught:
            ocr._resolve_layout(words, xs, ys)
        self.assertEqual(caught.exception.error_code, "OCR_LAYOUT_UNCONFIRMED")
        self.assertNotIn("private", json.dumps(caught.exception.diagnostics))
        self.assertEqual(caught.exception.diagnostics["candidate_count"], 0)


class GuidedRealOcrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, env = ocr._ocr_paths()
        if not (Path(env.get("TESSDATA_PREFIX", "")) / "kor.traineddata").is_file():
            raise unittest.SkipTest("Korean OCR model is not installed")

    def test_confirmed_geometry_recovers_when_automatic_grid_fails(self):
        pix, layout = gridless_capture()
        image = pix.tobytes("png")
        with self.assertRaises(ocr.LayoutRecognitionError) as failed:
            ocr.parse_image(image, "pseudonyms.png")
        self.assertEqual(failed.exception.error_code, "OCR_GRID_NOT_FOUND")
        result = ocr.parse_image_with_layout(image, "pseudonyms.png", layout)
        first, second = result["trips"]
        self.assertEqual((first["name"], second["name"]), ("가명인", "나명인"))
        self.assertEqual((first["start_time"], first["end_time"]), ("10:57", "15:06"))
        self.assertEqual((second["start_time"], second["end_time"]), ("09:11", "11:22"))
        self.assertEqual(result["input_method"], "confirmed_layout")
        self.assertEqual(result["input_kind"], "image")
        self.assertEqual(result["approval_policy"], "from_image")
        self.assertNotIn("999999", json.dumps(result))
        self.assertNotIn("888888", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
