"""Pseudonym-only report fixtures; private uploaded files are never copied here."""
from pathlib import Path
import json
import sys
import unittest

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
import monthly_report as report
from renderer import recalculate


def synthetic_monthly_pdf(rows=None):
    if rows is None:
        local = [("09:39", "13:48", "사용"), ("10:35", "14:42", "미사용"),
                 ("09:34", "14:22", "사용"), ("10:26", "14:14", "미사용"),
                 ("10:58", "15:11", "미사용"), ("10:32", "15:11", "미사용"),
                 ("10:06", "14:14", "미사용"), ("10:41", "15:18", "사용"),
                 ("11:18", "16:06", "사용")]
        rows = []
        for i, (start, end, car) in enumerate(local, 1):
            day = 31-i
            rows.append([str(i), "시험지원과\n지방농촌지도사", "홍길동\n(TEST0001)",
                         f"2026-10-{day:02d} {start}\n~ 2026-10-{day:02d} {end}",
                         "4시간", "근무지내", car, "가상읍", "현장 점검", "제2호",
                         "0", "0", "0", "0", "0", "0", "0", "0"])
        for i in range(3):
            rows.append([str(10+i), "시험지원과\n지방농촌지도사", "홍길동\n(TEST0001)",
                         f"2026-10-{10-i:02d} 09:00\n~ 2026-10-{11-i:02d} 18:00",
                         "2일", "근무지외", "미사용", "가상군", "업무 협의", "제2호",
                         "0", "0", "0", "0", "0", "0", "0", "0"])
    document = fitz.open()
    page = document.new_page(width=1200, height=max(250, 100+len(rows)*34))
    page.insert_font(fontname="ko", fontfile=str(ROOT / "app/fonts/NotoSansCJKkr-Regular.otf"))
    page.insert_text((20, 25), "출장 월별집계 (가명 시험)", fontname="ko", fontsize=15)
    widths = [28,100,68,155,64,54,70,66,100,35,38,38,38,38,38,38,42,49]
    factor=1160/sum(widths)
    xs=[20]
    for width in widths:
        xs.append(xs[-1]+width*factor)
    headers=["순번", "소속/직급", "성명", "출장기간", "총출장시간", "구분", "공무용차량", "출장지", "출장목적", "여비등급", "일비", "식비", "숙박비", "교통비", "현지교통비", "기타", "합계", "청구액(수령액)"]
    ys=[45,75]+[75+34*(i+1) for i in range(len(rows))]
    for x in xs:
        page.draw_line((x,45),(x,ys[-1]),color=(.55,.55,.55),width=.5)
    for y in ys:
        page.draw_line((20,y),(1180,y),color=(.55,.55,.55),width=.5)
    for ri,cells in enumerate([headers]+rows):
        for ci,text in enumerate(cells):
            rect=fitz.Rect(xs[ci]+2,ys[ri]+2,xs[ci+1]-2,ys[ri+1]-2)
            page.insert_textbox(rect,text,fontname="ko",fontsize=7.3 if ri else 7,align=0)
    document.subset_fonts(fallback=True)
    return document.tobytes(garbage=4,deflate=True)


class MonthlyPdfTests(unittest.TestCase):
    def test_local_amounts_and_outside_default_exclusion(self):
        result=report.parse_pdf(synthetic_monthly_pdf(),"monthly.pdf")
        trips=result["trips"]
        self.assertEqual(len(trips),12)
        self.assertEqual(sum(t["include"] for t in trips),9)
        self.assertEqual({t["name"] for t in trips},{"홍길동"})
        self.assertEqual({t["rank"] for t in trips},{"지방농촌지도사"})
        self.assertTrue(all(t["approval_status"]=="" for t in trips))
        self.assertTrue(all(t["daily_allowance"]==0 for t in trips))
        calculated=recalculate(trips)["trips"]
        self.assertEqual(sum(t["claim_amount"] or 0 for t in calculated if t["include"]),130000)
        self.assertNotIn("TEST0001",json.dumps(result,ensure_ascii=False))
        self.assertTrue(result["preview_data"])

    def test_unknown_rank_and_missing_end_are_not_invented(self):
        row=["1","이름만있는부서","가명인\n(A123456)","2026-10-22 09:00","", "근무지내", "미사용", "가상읍", "확인", "제2호"]+["0"]*8
        trip,notes=report._parse_cells(row,1)
        self.assertEqual(trip["rank"],"")
        self.assertEqual(trip["end_date"],"")
        self.assertEqual(trip["end_time"],"")
        self.assertFalse(trip["include"])
        self.assertTrue(notes)
        self.assertEqual(trip["name"],"가명인")

    def test_local_transport_preserved_as_combined_expense(self):
        row=["1","시험과/주무관","가명인","2026-10-22 09:00 ~ 2026-10-22 14:00","5시간", "근무지내", "미사용", "가상읍", "확인", "제2호","0","0","0","1,000","2,000","0","3,000","3,000"]
        trip,notes=report._parse_cells(row,1)
        self.assertEqual(trip["transport"],3000)
        self.assertEqual(trip["claim_amount"],3000)
        self.assertTrue(any("합산" in note for note in notes))

    def test_surviving_end_time_does_not_become_start(self):
        start,end,start_time,end_time=report._period("~ 2026-10-22 14:00")
        self.assertEqual((start,start_time),("",""))
        self.assertEqual((end,end_time),("2026-10-22","14:00"))
        self.assertEqual(report._period("2026-10-22 14:00"),("","","",""))

    def test_encrypted_pdf_and_unrelated_document_rejected(self):
        doc=fitz.open(stream=synthetic_monthly_pdf(),filetype="pdf")
        encrypted=doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,user_pw="fixture-only")
        with self.assertRaisesRegex(ValueError,"암호"):
            report.parse_pdf(encrypted)
        blank=fitz.open();blank.new_page()
        with self.assertRaisesRegex(ValueError,"월별집계"):
            report.parse_pdf(blank.tobytes())
        with self.assertRaises(ValueError):
            report.parse_pdf(b"not a pdf")


if __name__=="__main__":
    unittest.main()
