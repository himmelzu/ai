"""Meaningful extraction checks; synthetic fixtures contain pseudonyms only."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fitz
from app import ocr_parser as ocr


class ParsingTests(unittest.TestCase):
    def cells(self, **kwargs):
        cells = {k: "" for k in ocr._COLUMNS}
        cells.update({"department": "시험부서", "rank": "주무관", "name": "가 명인", "identifier": "999999", "actual": "시작 10:57\n종료 15:06", "requested": "2026-10-22 09:30 ~\n2026-10-22 17:00", "destination": "시험출장지", "purpose": "현장 점검", "vehicle": "미사용", "approval": "결재완료"})
        cells.update(kwargs)
        return cells

    def test_actual_times_do_not_use_application_times(self):
        trip, notes = ocr._parse_cells(self.cells(), 1)
        self.assertEqual((trip["start_time"], trip["end_time"]), ("10:57", "15:06"))
        self.assertEqual((trip["start_date"], trip["end_date"]), ("2026-10-22", "2026-10-22"))
        self.assertEqual(trip["name"], "가명인")
        self.assertEqual(trip["vehicle"], "unused")
        self.assertFalse(notes)
        self.assertNotIn("identifier", trip)
        self.assertNotIn("999999", str(trip))

    def test_missing_actual_times_are_not_invented(self):
        trip, notes = ocr._parse_cells(self.cells(actual=""), 2)
        self.assertEqual((trip["start_time"], trip["end_time"]), ("", ""))
        self.assertIn("신청시간으로 대체하지", notes[0])

    def test_end_only_stays_end_only(self):
        trip, notes = ocr._parse_cells(self.cells(actual="종료 15:06"), 1)
        self.assertEqual((trip["start_time"], trip["end_time"]), ("", "15:06"))
        self.assertTrue(notes)

    def test_vehicle_no_substring_confusion(self):
        for cell, expected in [("미사용", "unused"), ("사용", "used"), ("N", "unused"), ("Y", "used"), ("", "unknown")]:
            with self.subTest(cell=cell):
                trip, _ = ocr._parse_cells(self.cells(vehicle=cell), 1)
                self.assertEqual(trip["vehicle"], expected)

    def test_invalid_clock_and_date_not_passed_through(self):
        trip, notes = ocr._parse_cells(self.cells(actual="시작 29:50 종료 15:06", requested="2026-02-30"), 1)
        self.assertEqual(trip["start_date"], "")
        self.assertNotEqual(trip["start_time"], "29:50")
        self.assertTrue(notes)

    def test_unreadable_end_date_is_not_replaced_with_start_date(self):
        trip, notes = ocr._parse_cells(self.cells(requested="2026-10-22 09:30 ~ 알수없음 17:00"), 1)
        self.assertEqual(trip["start_date"], "2026-10-22")
        self.assertEqual(trip["end_date"], "")
        self.assertTrue(any("종료 날짜" in note for note in notes))

    def test_invalid_image(self):
        for data in [b"", b"not an image", b"%PDF-1.4"]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                ocr.parse_image(data)

    def test_no_table_fail_fast(self):
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 900, 200))
        pix.clear_with(255)
        with patch.object(ocr, "_recognize") as recognizer, self.assertRaisesRegex(ValueError, "표를 찾지"):
            ocr.parse_image(pix.tobytes("png"))
        recognizer.assert_not_called()


def synthetic_capture(scale=1):
    """Draw a 12-column screenshot with two people and deliberately differing times."""
    doc = fitz.open()
    page = doc.new_page(width=1500, height=175)
    font = Path(__file__).resolve().parents[1] / "app" / "fonts" / "NotoSansCJKkr-Regular.otf"
    if not font.is_file():
        font = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    page.insert_font(fontname="ko", fontfile=str(font))
    widths = [40, 110, 90, 90, 95, 150, 245, 125, 190, 110, 110, 145]
    xs = [0]
    for width in widths:
        xs.append(xs[-1] + width)
    ys = [1, 45, 105, 174]
    for x in xs:
        page.draw_line((x if x else 1, 1), (x if x else 1, 174), color=(0.65, 0.65, 0.65), width=0.7)
    for y in ys:
        page.draw_line((1, y), (1500, y), color=(0.65, 0.65, 0.65), width=0.7)
    rows = [
        ["No.", "부서", "직급", "출장자", "식별번호", "출장 시작종료확인", "신청 출장기간", "출장지", "출장목적", "여비등급", "공용차량", "결재상태"],
        ["1", "시험부서", "주무관", "가명인", "999999", "시작 10:57\n종료 15:06", "2026-10-22 09:30\n2026-10-22 17:00", "시험출장지", "현장 점검", "2등급", "미사용", "결재완료"],
        ["2", "시험부서", "주무관", "나명인", "888888", "시작 09:11\n종료 11:22", "2026-10-23 09:00\n2026-10-23 12:00", "시험출장지", "업무 협의", "2등급", "사용", "결재완료"],
    ]
    for ri, row in enumerate(rows):
        for ci, text in enumerate(row):
            for li, line in enumerate(text.split("\n")):
                page.insert_text((xs[ci] + 5, ys[ri] + 24 + li * 20), line, fontname="ko", fontsize=11)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return pix.tobytes("png")


def synthetic_ui_capture(scale=1, empty=False):
    """1244px UI shape: checkbox, split actual-time cells, clipped trailing column."""
    doc = fitz.open()
    page = doc.new_page(width=1244, height=381)
    font = Path(__file__).resolve().parents[1] / "app" / "fonts" / "NotoSansCJKkr-Regular.otf"
    if not font.is_file():
        font = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    page.insert_font(fontname="ko", fontfile=str(font))
    xs = [4, 43, 93, 225, 327, 373, 443, 524, 604, 825, 919, 1076, 1123, 1171, 1234, 1243]
    ys = [1, 44] + [44 + i * 33.5 for i in range(1, 11)]
    for index, x in enumerate(xs):
        page.draw_line((x, 25 if index == 7 else 1), (x, 379), color=(0.78, 0.78, 0.78), width=0.65)
    for y in ys:
        page.draw_line((4, y), (1243, y), color=(0.78, 0.78, 0.78), width=0.65)
    page.draw_line((443, 25), (604, 25), color=(0.78, 0.78, 0.78), width=0.65)
    headers = ["", "No.", "부서", "직급", "출장자", "식별번호", "출장 시작종료확인", "", "신청 출장기간", "출장지", "출장목적", "여비등급", "공용차량", "결재상태", ""]
    for index, text in enumerate(headers):
        page.insert_text((xs[index] + 3, 17), text, fontname="ko", fontsize=9)
    page.insert_text((460, 37), "시작", fontname="ko", fontsize=8)
    page.insert_text((540, 37), "종료", fontname="ko", fontsize=8)
    if not empty:
        for ri, initial in enumerate("가나다라마바사아자차"):
            y = ys[ri + 1]
            cells = ["", str(ri + 1), "시험부서", "주무관", initial + "명인", "999999", "시작10:57", "종료15:06", "2026-10-22 09:30\n2026-10-22 17:00", "시험출장지", "현장 점검", "2등급", "미사용" if ri % 2 == 0 else "사용", "결재완료", ""]
            for ci, text in enumerate(cells):
                for li, line in enumerate(text.split("\n")):
                    baseline = y + (12 + li * 15 if ci == 8 else 22)
                    page.insert_text((xs[ci] + 3, baseline), line, fontname="ko", fontsize=11)
    return page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")


def synthetic_realistic_capture():
    """UI heading, 56px tinted two-tier header, 28px rows, single-line periods."""
    doc = fitz.open()
    page = doc.new_page(width=1244, height=381)
    font = Path(__file__).resolve().parents[1] / "app" / "fonts" / "NotoSansCJKkr-Regular.otf"
    if not font.is_file():
        font = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    page.insert_font(fontname="ko", fontfile=str(font))
    page.insert_text((5, 22), "근무지내출장처리 목록 [ TOTAL 10 / 10 ]", fontname="ko", fontsize=12)
    xs = [4, 43, 93, 225, 327, 373, 443, 524, 604, 825, 919, 1076, 1123, 1171, 1234]
    ys = [33, 89] + [89 + 28 * i for i in range(1, 11)]
    page.draw_rect(fitz.Rect(4, 33, 1244, 89), color=None, fill=(0.965, 0.965, 0.965))
    for i, x in enumerate(xs):
        page.draw_line((x, 65 if i == 7 else 33), (x, 369), color=(0.86, 0.87, 0.89), width=0.6)
    for y in ys:
        page.draw_line((4, y), (1244, y), color=(0.86, 0.87, 0.89), width=0.6)
    page.draw_line((443, 65), (604, 65), color=(0.86, 0.87, 0.89), width=0.6)
    headers = ["", "No.", "부서", "직급", "출장자", "식별번호", "출장 시작/종료 확인", "", "신청 출장기간", "출장지", "출장목적", "여비등급", "공용차량", "결재상태"]
    for i, text in enumerate(headers):
        page.insert_text((xs[i] + 4, 53 if i == 6 else 68), text, fontname="ko", fontsize=10)
    page.insert_text((460, 81), "시작", fontname="ko", fontsize=10)
    page.insert_text((540, 81), "종료", fontname="ko", fontsize=10)
    rows = [("22", "10:57", "15:06", "미사용"), ("21", "10:10", "14:12", "미사용"), ("20", "11:03", "15:00", "미사용"), ("15", "09:49", "14:15", "미사용"), ("14", "11:17", "15:20", "미사용"), ("13", "10:44", "15:38", "미사용"), ("08", "10:19", "15:30", "사용"), ("06", "10:46", "15:24", "미사용"), ("02", "09:23", "13:50", "미사용"), ("01", "09:21", "10:30", "사용")]
    for ri, (day, start, end, car) in enumerate(rows):
        y = 89 + ri * 28
        cells = ["", str(ri + 1), "시험지원과", "지방농촌지도사", "홍길동", "TEST0001", "시작 " + start, "종료 " + end, f"2026-10-{day} 09:30~2026-10-{day} 17:00", "가상, 시험", "청년농업인 현장 점검", "제2호", car, "미신청" if ri == 9 else "결재완료"]
        for ci, text in enumerate(cells):
            page.insert_text((xs[ci] + 4, y + 18), text, fontname="ko", fontsize=10.3 if ci == 8 else 11)
    return page.get_pixmap(alpha=False).tobytes("png")


class RealOcrTests(unittest.TestCase):
    def test_synthetic_two_people_end_to_end(self):
        executable, env = ocr._ocr_paths()
        bundled = Path(env.get("TESSDATA_PREFIX", "")) / "kor.traineddata"
        if not bundled.is_file():
            self.skipTest("Bundled Korean model is not installed yet")
        for scale in [1, 1.35]:
            with self.subTest(scale=scale):
                result = ocr.parse_image(synthetic_capture(scale), "test.png")
                self.assertEqual(len(result["trips"]), 2)
                first, second = result["trips"]
                self.assertEqual(first["name"], "가명인")
                self.assertEqual(first["start_time"], "10:57")
                self.assertEqual(first["end_time"], "15:06")
                self.assertEqual(second["start_date"], "2026-10-23")
                self.assertEqual((second["start_time"], second["end_time"]), ("09:11", "11:22"))
                self.assertNotIn("999999", str(result))
                self.assertNotIn("888888", str(result))

    def test_ui_checkbox_split_actual_and_clipped_extra_column(self):
        _, env = ocr._ocr_paths()
        if not (Path(env.get("TESSDATA_PREFIX", "")) / "kor.traineddata").is_file():
            self.skipTest("Bundled Korean model is not installed yet")
        for scale in [1, 1.2]:
            with self.subTest(scale=scale):
                result = ocr.parse_image(synthetic_ui_capture(scale), "pseudonyms.png")
                self.assertEqual(len(result["trips"]), 10)
                self.assertEqual([t["name"] for t in result["trips"]], [initial + "명인" for initial in "가나다라마바사아자차"])
                for ri, trip in enumerate(result["trips"]):
                    self.assertEqual((trip["start_time"], trip["end_time"]), ("10:57", "15:06"))
                    self.assertEqual(trip["requested_start_time"], "09:30")
                    self.assertEqual(trip["start_date"], "2026-10-22")
                    self.assertEqual(trip["rank"], "주무관")
                    self.assertEqual(trip["vehicle"], "unused" if ri % 2 == 0 else "used")
                sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
                from renderer import recalculate
                calculated = recalculate(result["trips"])
                self.assertEqual(sum(t["daily_allowance"] or 0 for t in calculated["trips"]), 150000)
                self.assertNotIn("999999", str(result))

    def test_realistic_28px_single_line_period_and_backend_total(self):
        _, env = ocr._ocr_paths()
        if not (Path(env.get("TESSDATA_PREFIX", "")) / "kor.traineddata").is_file():
            self.skipTest("Bundled Korean model is not installed yet")
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
        from backend import parse_capture
        result = parse_capture(synthetic_realistic_capture(), "realistic-pseudonyms.png")
        trips = result["trips"]
        self.assertEqual(len(trips), 10)
        self.assertEqual({t["name"] for t in trips}, {"홍길동"})
        self.assertEqual({t["rank"] for t in trips}, {"지방농촌지도사"})
        self.assertEqual(trips[0]["start_time"], "10:57")
        self.assertEqual(trips[2]["end_time"], "15:00")
        self.assertFalse(trips[-1]["include"])
        self.assertEqual(trips[-1]["approval_status"], "미신청")
        self.assertEqual([t["vehicle"] for t in trips], ["unused"] * 6 + ["used", "unused", "unused", "used"])
        self.assertEqual(sum(t["daily_allowance"] or 0 for t in trips if t["include"]), 160000)
        self.assertTrue(any("성명 인식 후보" in note for note in result["warnings"]))
        self.assertNotIn("TEST0001", str(result))

    def test_ui_empty_rows_do_not_validate_as_trips(self):
        _, env = ocr._ocr_paths()
        if not (Path(env.get("TESSDATA_PREFIX", "")) / "kor.traineddata").is_file():
            self.skipTest("Bundled Korean model is not installed yet")
        with self.assertRaisesRegex(ValueError, "출장 내역이 없습니다"):
            ocr.parse_image(synthetic_ui_capture(empty=True))


if __name__ == "__main__":
    unittest.main()
