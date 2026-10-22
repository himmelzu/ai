"""Header recovery tests. All generated records are pseudonyms, not uploaded data."""
from pathlib import Path
import re
import sys
import types
import unittest
from unittest.mock import patch

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import ocr_parser as ocr
from test_ocr_parser import synthetic_realistic_capture


def words_for_rows(rows, *, widths=None):
    widths = widths or [45, 110, 85, 65, 90, 145, 245, 125, 175, 65, 80, 100]
    xs = [1.0]
    for width in widths:
        xs.append(xs[-1] + width)
    ys = [1.0 + 42 * index for index in range(len(rows) + 1)]
    words = []
    for ri, row in enumerate(rows):
        for ci, text in enumerate(row):
            if text:
                words.append(ocr.Word(text, xs[ci] + 3, ys[ri] + 8, min(xs[ci + 1] - xs[ci] - 6, 40), 14, 85))
    return words, xs, ys


def data_row(number="1", *, identifier="TEST0001", actual="시작 10:57 종료 15:06"):
    return [number, "시험지원과", "주무관", "가명인", identifier, actual,
            "2026-10-22 09:30 ~ 2026-10-22 17:00", "시험읍", "현장 확인", "제2호", "미사용", "결재완료"]


class HeaderLayoutTests(unittest.TestCase):
    def test_missing_headings_recover_only_after_body_cross_checks(self):
        words, xs, ys = words_for_rows([["?"] * 12, data_row(), data_row("2")])
        layout = ocr._resolve_layout(words, xs, ys)
        self.assertTrue(layout.recovered)
        self.assertEqual(layout.first_data_row, 1)
        self.assertEqual(layout.xs, xs)
        trips, _ = ocr._parse_table(words, layout.xs, ys, first_data_row=layout.first_data_row)
        self.assertEqual(len(trips), 2)
        self.assertEqual((trips[0]["start_time"], trips[0]["end_time"]), ("10:57", "15:06"))
        self.assertEqual(trips[0]["requested_start_time"], "09:30")

    def test_hidden_identifier_does_not_require_exposing_it(self):
        words, xs, ys = words_for_rows([["?"] * 12, data_row(identifier=""), data_row("2", identifier="")])
        layout = ocr._resolve_layout(words, xs, ys)
        self.assertTrue(layout.recovered)

    def test_unreadable_small_row_numbers_do_not_block_other_strong_evidence(self):
        words, xs, ys = words_for_rows([["?"] * 12, data_row("", identifier="TESTOOO1"), data_row("", identifier="TESTOOO1")])
        layout = ocr._resolve_layout(words, xs, ys)
        self.assertTrue(layout.recovered)
        trips, _ = ocr._parse_table(words, layout.xs, ys, first_data_row=layout.first_data_row)
        self.assertEqual(len(trips), 2)

    def test_partly_unreadable_first_trip_is_not_dropped_after_readable_headers(self):
        labels = ["No.", "부서", "직급", "출장자", "식별번호", "출장 시작종료확인", "신청 출장기간", "출장지", "출장목적", "여비등급", "공용차량", "결재상태"]
        partial = data_row("?", actual="")
        partial[3], partial[6] = "가명하나", "날짜 인식실패"
        second = data_row("2")
        second[3] = "가명둘"
        words, xs, ys = words_for_rows([labels, partial, second])
        layout = ocr._resolve_layout(words, xs, ys)
        self.assertEqual(layout.first_data_row, 1)
        trips, _ = ocr._parse_table(words, layout.xs, ys, first_data_row=layout.first_data_row)
        self.assertEqual([t["name"] for t in trips], ["가명하나", "가명둘"])
        self.assertEqual(trips[0]["start_time"], "")
        self.assertTrue(trips[0]["ocr_warnings"])

    def test_partly_unreadable_first_trip_is_retained_during_body_recovery(self):
        partial = data_row("?", actual="")
        partial[3], partial[6] = "가명하나", "날짜 인식실패"
        words, xs, ys = words_for_rows([["?"] * 12, partial, data_row("2"), data_row("3")])
        layout = ocr._resolve_layout(words, xs, ys)
        self.assertEqual(layout.first_data_row, 1)
        trips, _ = ocr._parse_table(words, layout.xs, ys, first_data_row=layout.first_data_row)
        self.assertEqual(len(trips), 3)
        self.assertEqual(trips[0]["name"], "가명하나")

    def test_missing_header_with_no_actual_times_is_not_guessed_from_requested_times(self):
        words, xs, ys = words_for_rows([["?"] * 12, data_row(actual=""), data_row("2", actual="")])
        with self.assertRaisesRegex(ValueError, "열 배치"):
            ocr._resolve_layout(words, xs, ys)

    def test_unrelated_table_with_dates_is_rejected(self):
        rows = [["?"] * 12,
                ["1", "상품", "분류", "물품명", "123456", "10개", "2026-10-22 ~ 2026-10-23", "주소", "판매", "제2호", "배송", "처리중"],
                ["2", "상품", "분류", "물품명", "123456", "15개", "2026-10-22 ~ 2026-10-23", "주소", "판매", "제2호", "배송", "처리중"]]
        words, xs, ys = words_for_rows(rows)
        with self.assertRaisesRegex(ValueError, "열 배치"):
            ocr._resolve_layout(words, xs, ys)

    def test_ambiguous_repeated_labels_are_not_silently_assigned(self):
        all_labels = "부서 직급 출장자 신청출장기간 출장지 출장목적 공용차량 결재상태"
        words, xs, ys = words_for_rows([[all_labels] * 15, [""] * 15], widths=[90] * 15)
        with self.assertRaisesRegex(ValueError, "두 가지 이상"):
            ocr._resolve_layout(words, xs, ys)

    def test_header_crop_is_optional_and_first_body_row_is_preserved(self):
        words, xs, ys = words_for_rows([data_row(), data_row("2")])
        layout = ocr._resolve_layout(words, xs, ys)
        self.assertEqual(layout.first_data_row, 0)
        trips, _ = ocr._parse_table(words, layout.xs, ys, first_data_row=layout.first_data_row)
        self.assertEqual(len(trips), 2)
        self.assertEqual(trips[0]["start_time"], "10:57")

    def test_title_date_range_does_not_create_a_phantom_trip(self):
        title = [""] * 12
        title[6] = "조회기간 2026-10-01 ~ 2026-10-31"
        labels = ["No.", "부서", "직급", "출장자", "식별번호", "출장 시작종료확인", "신청 출장기간", "출장지", "출장목적", "여비등급", "공용차량", "결재상태"]
        words, xs, ys = words_for_rows([title, labels, data_row(), data_row("2")])
        layout = ocr._resolve_layout(words, xs, ys)
        trips, _ = ocr._parse_table(words, layout.xs, ys, first_data_row=layout.first_data_row)
        self.assertEqual(len(trips), 2)
        self.assertEqual(layout.first_data_row, 2)

    def test_missing_name_remains_editable_instead_of_discarding_all_rows(self):
        cells = dict(zip(ocr._COLUMNS, data_row()))
        cells["name"] = ""
        trip, notes = ocr._parse_cells(cells, 1)
        self.assertEqual(trip["name"], "")
        self.assertTrue(any("이름" in note for note in notes))
        self.assertEqual(trip["start_time"], "10:57")

    def test_retry_never_turns_one_actual_time_into_two_duplicate_times(self):
        partial = data_row("1", actual="시작 10:57")
        partial[6] = "날짜 인식실패"
        words, xs, ys = words_for_rows([["?"] * 12, partial, data_row("2"), data_row("3")])
        # This emulates the previous overly tall header crop, which also saw
        # the first partial trip. Even such a retry must not duplicate data.
        retry = [w for w in words if w.top + w.height / 2 < ys[2]]
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, int(xs[-1]) + 1, int(ys[-1]) + 1))
        pix.clear_with(255)
        with patch.object(ocr, "_grid", return_value=(xs, ys)), \
                patch.object(ocr, "_recognize", return_value=words), \
                patch.object(ocr, "_recognize_header", return_value=retry), \
                patch.object(ocr, "_refine_identity_columns", side_effect=lambda pix, words, *args: (words, {})), \
                patch.object(ocr, "_refine_actual_times", side_effect=lambda pix, words, *args: words):
            result = ocr.parse_image(pix.tobytes("png"))
        first = result["trips"][0]
        self.assertEqual((first["start_time"], first["end_time"]), ("10:57", ""))
        self.assertTrue(any("실제 시작·종료" in note for note in first["ocr_warnings"]))

    def test_one_damaged_long_label_does_not_need_a_spelling_whitelist(self):
        self.assertGreater(ocr._header_score("신청 출잠기간", "requested"), 0)
        self.assertEqual(ocr._header_score("목적지", "purpose"), 0)
        self.assertEqual(ocr._header_score("거래기간", "requested"), 0)

    def test_monthly_report_route_precedes_ui_layout(self):
        result = {"trips": [{"name": "가명인"}], "warnings": [], "source_name": "monthly.png"}
        helper = types.ModuleType("app.monthly_report")
        helper.try_parse_monthly_image = lambda *args: result
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 1000, 200))
        pix.clear_with(255)
        with patch.dict(sys.modules, {"app.monthly_report": helper}), \
                patch.object(ocr, "_grid", return_value=([float(x * 50) for x in range(19)], [1.0, 90.0, 199.0])), \
                patch.object(ocr, "_recognize", return_value=[ocr.Word("월별집계", 1, 4, 40, 10, 90)]), \
                patch.object(ocr, "_resolve_layout") as ui_layout:
            parsed = ocr.parse_image(pix.tobytes("png"), "monthly.png")
        self.assertEqual(parsed, result)
        ui_layout.assert_not_called()


def hidden_header_capture():
    pix = fitz.Pixmap(synthetic_realistic_capture())
    pix.set_rect(fitz.IRect(5, 34, 1233, 88), (255, 255, 255))
    return pix


class HeaderRecoveryRealOcrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, env = ocr._ocr_paths()
        if not (Path(env.get("TESSDATA_PREFIX", "")) / "kor.traineddata").is_file():
            raise unittest.SkipTest("Korean OCR model is not installed")

    def test_hidden_header_png_still_reaches_review_with_warning(self):
        result = ocr.parse_image(hidden_header_capture().tobytes("png"), "hidden-header.png")
        self.assertEqual(len(result["trips"]), 10)
        self.assertEqual((result["trips"][0]["start_time"], result["trips"][0]["end_time"]), ("10:57", "15:06"))
        self.assertTrue(any("열 배치" in note for note in result["warnings"]))
        self.assertTrue(any("열 배치" in note for note in result["trips"][0]["ocr_warnings"]))

    def test_jpeg_compression_does_not_require_perfect_header_labels(self):
        result = ocr.parse_image(hidden_header_capture().tobytes("jpeg", jpg_quality=78), "compressed.jpg")
        self.assertEqual(len(result["trips"]), 10)
        self.assertNotEqual(result["trips"][0]["start_time"], "09:30")
        self.assertTrue(any("열 배치" in note for note in result["warnings"]))

    def test_small_screenshot_does_not_substitute_requested_times(self):
        source = hidden_header_capture()
        pix = fitz.Pixmap(source, round(source.width * 0.85), round(source.height * 0.85))
        result = ocr.parse_image(pix.tobytes("png"), "small.png")
        self.assertEqual(len(result["trips"]), 10)
        # Missing OCR is reviewable; planned times must never become actual times.
        self.assertIn(result["trips"][0]["start_time"], ("10:57", ""))
        self.assertNotEqual(result["trips"][0]["start_time"], "09:30")

    def test_large_top_margin_is_not_part_of_the_column_header(self):
        pix = hidden_header_capture()
        doc = fitz.open()
        page = doc.new_page(width=pix.width, height=pix.height + 320)
        page.insert_image(fitz.Rect(0, 320, pix.width, pix.height + 320), stream=pix.tobytes("png"))
        result = ocr.parse_image(page.get_pixmap(alpha=False).tobytes("png"), "top-margin.png")
        self.assertEqual(len(result["trips"]), 10)
        self.assertEqual(result["trips"][0]["start_time"], "10:57")


if __name__ == "__main__":
    unittest.main()
