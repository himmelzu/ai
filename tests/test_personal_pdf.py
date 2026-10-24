"""Pseudonym-only personal export fixtures; uploaded travel records stay private."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import monthly_report as report
from renderer import recalculate


HEADERS = ["순번", "구분", "", "출발일자", "도착일자", "총출장시간", "출장지",
           "차량", "출장목적", "소속", "출장자", "지출", "여비", "지출일자",
           "결재상태", "여비등급", "비고", "업무대행"]


def personal_source_rows():
    """Ten trips, with requested times deliberately unlike their actual times."""
    rows = []
    for index in range(10):
        day = f"2026-11-{10-index:02d}"
        actual_start, actual_end = ("09:10", "13:15") if index == 0 else ("09:30", "12:00") if index == 1 else ("09:00", "14:00")
        car = "사용" if index == 6 else "미사용"
        for kind, start, end in (("신청", "08:00", "18:00"), ("확인", actual_start, actual_end)):
            status = "미전송" if index == 9 and kind == "확인" else "결재완료"
            rows.append([str(len(rows)+1), "근무지내", kind, f"{day}\n{start}", f"{day}\n{end}",
                         "5시간", "가상읍", car, "가명 현장 확인", "시험지원과", "가명인(TEST0001)",
                         "0", "0", "", status, "제2호", "", ""])
    return rows


def synthetic_personal_pdf(rows=None, *, split=13, shifted_continuation=False, headers=None):
    rows = personal_source_rows() if rows is None else rows
    batches = [rows[:split], rows[split:]] if len(rows) > split else [rows]
    document = fitz.open()
    for number, body in enumerate(batches):
        page = document.new_page(width=1400, height=max(300, 100+len(body)*34))
        page.insert_font(fontname="ko", fontfile=str(ROOT / "app/fonts/NotoSansCJKkr-Regular.otf"))
        page.insert_text((20, 25), "개인별 출장 내역 (가명 시험)", fontname="ko", fontsize=15)
        # The second page intentionally omits the header, like the real export.
        table_rows = [HEADERS if headers is None else headers]+body if number == 0 else body
        widths = [30,55,42,105,105,70,95,50,140,80,90,40,40,70,65,50,50,65]
        factor = 1360/sum(widths)
        offset = 12 if shifted_continuation and number else 0
        xs = [20+offset]
        for width in widths:
            xs.append(xs[-1]+width*factor)
        ys = [45+34*index for index in range(len(table_rows)+1)]
        for x in xs:
            page.draw_line((x,ys[0]),(x,ys[-1]),color=(.55,.55,.55),width=.5)
        for y in ys:
            page.draw_line((xs[0],y),(xs[-1],y),color=(.55,.55,.55),width=.5)
        for ri,cells in enumerate(table_rows):
            for ci,text in enumerate(cells):
                page.insert_textbox(fitz.Rect(xs[ci]+2,ys[ri]+2,xs[ci+1]-2,ys[ri+1]-2),text,
                                    fontname="ko",fontsize=7.2 if ri or number else 7,align=0)
    document.subset_fonts(fallback=True)
    return document.tobytes(garbage=4,deflate=True)


class PersonalPdfTests(unittest.TestCase):
    def test_pairs_across_page_break_use_actual_times_and_actual_status(self):
        result = report.parse_pdf(synthetic_personal_pdf(), "personal.pdf")
        trips = result["trips"]
        self.assertEqual(result["input_format"], "personal_trip_list")
        self.assertEqual(result["approval_policy"], "source_confirmation_status")
        self.assertEqual(result["source_summary"], {"source_rows":20,"trip_count":10,
                         "confirmation_count":10,"application_only_count":0,"excluded_count":1})
        self.assertEqual((trips[0]["start_time"],trips[0]["end_time"]),("09:10","13:15"))
        self.assertEqual((trips[0]["requested_start_time"],trips[0]["requested_end_time"]),("08:00","18:00"))
        # Request row 13 is on page 1, its confirmation row 14 on page 2.
        self.assertEqual((trips[6]["requested_start_time"],trips[6]["start_time"]),("08:00","09:00"))
        self.assertEqual(trips[6]["vehicle"], "used")
        self.assertEqual(trips[-1]["approval_status"], "미전송")
        self.assertFalse(trips[-1]["include"])
        self.assertTrue(all(trip["rank"] == "" for trip in trips))
        self.assertTrue(all(trip["name"] == "가명인" for trip in trips))
        self.assertNotIn("TEST0001",str(result))
        calculated = recalculate(trips)["trips"]
        self.assertEqual(sum(trip["claim_amount"] or 0 for trip in calculated if trip["include"]),160000)

    def test_application_only_does_not_supply_actual_dates_or_vehicle(self):
        result = report.parse_pdf(synthetic_personal_pdf(personal_source_rows()[:1]))
        trip = result["trips"][0]
        self.assertFalse(trip["include"])
        for key in ("start_date","end_date","start_time","end_time"):
            self.assertEqual(trip[key], "")
        self.assertEqual(trip["vehicle"], "unknown")
        self.assertEqual(trip["requested_start_time"], "08:00")
        self.assertEqual(result["source_summary"]["application_only_count"],1)

    def test_nonzero_or_missing_source_amounts_are_not_invented_as_zero(self):
        for value in ("40,000", ""):
            with self.subTest(value=value):
                row = deepcopy(personal_source_rows()[1]);row[12]=value
                trip, notes = report._personal_trip(row,1)
                self.assertTrue(trip["include"])
                self.assertTrue(all(trip[key] is None for key in ("meal","lodging","transport","other")))
                self.assertIsNone(recalculate([trip])["trips"][0]["claim_amount"])
                self.assertTrue(any("세부 경비" in note for note in notes))

    def test_standalone_confirmation_remains_usable_but_duplicate_rows_rejected(self):
        actual = personal_source_rows()[1]
        parsed = report.parse_pdf(synthetic_personal_pdf([actual]))
        self.assertEqual(len(parsed["trips"]),1)
        self.assertTrue(parsed["trips"][0]["include"])
        self.assertNotIn("requested_start_time",parsed["trips"][0])
        with self.assertRaisesRegex(ValueError,"순번"):
            report.parse_pdf(synthetic_personal_pdf([actual,actual]))

    def test_changed_headerless_continuation_is_not_silently_dropped(self):
        with self.assertRaisesRegex(ValueError,"다음 페이지"):
            report.parse_pdf(synthetic_personal_pdf(shifted_continuation=True))

    def test_missing_continuation_column_does_not_return_a_partial_claim(self):
        with fitz.open(stream=synthetic_personal_pdf(), filetype="pdf") as document:
            # Remove one of page 2's vertical table rules. Its seven travel
            # records remain readable as text, but find_tables sees 17 columns.
            document.update_stream(document[1].get_contents()[3], b"")
            self.assertEqual(document[1].find_tables().tables[0].col_count,17)
            broken=document.tobytes(garbage=4,deflate=True)
        with self.assertRaisesRegex(ValueError,"누락"):
            report.parse_pdf(broken)

    def test_monthly_continuation_with_missing_column_is_not_silently_dropped(self):
        headers=["순번", "소속/직급", "성명", "출장기간", "총출장시간", "구분", "공무용차량", "출장지", "출장목적", "여비등급", "일비", "식비", "숙박비", "교통비", "현지교통비", "기타", "합계", "청구액(수령액)"]
        # Different table heights prevent PDF stream deduplication from making
        # the removed second-page rule modify the first page as well.
        rows=[[str(index),"시험과/주무관","가명인","2026-11-10 09:00 ~ 2026-11-10 14:00",
               "5시간","근무지내","미사용","가상읍","가명 현장 확인","제2호"]+["0"]*8 for index in range(1,5)]
        source=synthetic_personal_pdf(rows,split=1,headers=headers)
        self.assertEqual(len(report.parse_pdf(source)["trips"]),4)
        with fitz.open(stream=source,filetype="pdf") as document:
            document.update_stream(document[1].get_contents()[3],b"")
            self.assertEqual(document[1].find_tables().tables[0].col_count,17)
            broken=document.tobytes(garbage=4,deflate=True)
        with self.assertRaisesRegex(ValueError,"누락"):
            report.parse_pdf(broken)

    def test_blank_and_footer_only_pages_are_not_mistaken_for_missing_trips(self):
        with fitz.open(stream=synthetic_personal_pdf(),filetype="pdf") as document:
            document.new_page(width=1400,height=300)
            page=document.new_page(width=1400,height=300)
            page.insert_font(fontname="ko",fontfile=str(ROOT/"app/fonts/NotoSansCJKkr-Regular.otf"))
            for x in (20,450,900,1380):
                page.draw_line((x,250),(x,280),width=.5)
            for y in (250,280):
                page.draw_line((20,y),(1380,y),width=.5)
            for left,text in ((25,"시험기관"),(455,"4/4"),(905,"가명인 출력정보 2026년 11월 20일")):
                page.insert_text((left,268),text,fontname="ko",fontsize=9)
            source=document.tobytes(garbage=4,deflate=True)
        result=report.parse_pdf(source)
        self.assertEqual(result["source_summary"]["source_rows"],20)
        self.assertEqual(len(result["trips"]),10)

    def test_unknown_numbered_row_kind_is_not_silently_dropped(self):
        rows=personal_source_rows()[:2];rows[1][2]="알수없음"
        with self.assertRaisesRegex(ValueError,"신청·확인"):
            report.parse_pdf(synthetic_personal_pdf(rows))


if __name__ == "__main__":
    unittest.main()
