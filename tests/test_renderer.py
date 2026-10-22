import copy
from pathlib import Path
import sys
import unittest
import unicodedata
import re

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from renderer import duration_minutes, recalculate, render_pdf


def trip(**kwargs):
    record = dict(department="가상지원과", rank="주무관", name="홍길동",
                  start_date="2026-10-22", end_date="2026-10-22", start_time="09:00", end_time="13:00",
                  scope="관내", vehicle="unused", destination="가상읍", purpose="현장 확인",
                  approval_status="결재완료", include=True, daily_allowance=None,
                  meal=0, lodging=0, transport=0, other=0, claim_amount=None)
    record.update(kwargs)
    return record


class RateTests(unittest.TestCase):
    def test_threshold_and_vehicle_deduction(self):
        cases = [("12:59", "unused", 10000), ("13:00", "unused", 20000),
                 ("12:59", "used", 0), ("13:00", "used", 10000)]
        for end, car, expected in cases:
            with self.subTest(end=end, car=car):
                result = recalculate([trip(end_time=end, vehicle=car)])
                self.assertEqual(result["trips"][0]["daily_allowance"], expected)

    def test_same_day_cap_and_separate_person(self):
        result = recalculate([trip(), trip(start_time="14:00", end_time="18:00"), trip(name="김가람")])
        self.assertEqual([r["daily_allowance"] for r in result["trips"]], [20000, 0, 20000])
        self.assertTrue(result["warnings"])

    def test_no_inference_for_missing_times_and_outside_area(self):
        for record in (trip(start_time=""), trip(vehicle="unknown"), trip(scope="관외"),
                       trip(end_date="2026-10-23"), trip(start_date="2026-02-30")):
            self.assertIsNone(recalculate([record])["trips"][0]["daily_allowance"])

    def test_excluded_rows_do_not_consume_cap(self):
        result = recalculate([trip(include=False), trip()])
        self.assertEqual(result["trips"][1]["daily_allowance"], 20000)

    def test_rank_typo_does_not_reset_person_day_cap(self):
        result = recalculate([trip(), trip(rank="주무 과")])
        self.assertEqual([t["daily_allowance"] for t in result["trips"]], [20000, 0])

    def test_invalid_money_and_threshold_are_rejected(self):
        for value in (-1, "nan", "inf", 1.5, True, 100000001):
            with self.assertRaises(ValueError):
                recalculate([trip(meal=value)])
        with self.assertRaises(ValueError):
            recalculate([trip()], {"threshold_minutes": 0})

    def test_original_data_not_mutated(self):
        record = trip()
        previous = copy.deepcopy(record)
        recalculate([record])
        self.assertEqual(record, previous)


class PdfTests(unittest.TestCase):
    def test_confirmed_pdf_contains_expected_content_and_sum(self):
        records = recalculate([trip(), trip(start_date="2026-10-21", end_date="2026-10-21", vehicle="used")])["trips"]
        payload, info = render_pdf(records, rates_confirmed=True, data_confirmed=True)
        self.assertFalse(info["is_draft"])
        with fitz.open(stream=payload, filetype="pdf") as doc:
            self.assertEqual(len(doc), 1)
            text = unicodedata.normalize("NFKC", doc[0].get_text())
            for expected in ("홍길동", "현장 확인", "30,000", "2건"):
                self.assertIn(expected, text)
            self.assertNotIn("검토용", text)

    def test_missing_data_never_produces_final_pdf(self):
        records = recalculate([trip(start_time="")])["trips"]
        payload, info = render_pdf(records, rates_confirmed=True, data_confirmed=True)
        self.assertTrue(info["is_draft"])
        with fitz.open(stream=payload, filetype="pdf") as doc:
            self.assertIn("확인 필요", unicodedata.normalize("NFKC", doc[0].get_text()))

    def test_unapproved_record_requires_review(self):
        records = recalculate([trip(approval_status="미신청")])["trips"]
        _, info = render_pdf(records, rates_confirmed=True, data_confirmed=True)
        self.assertTrue(info["is_draft"])
        self.assertTrue(any("결재상태" in w for w in info["warnings"]))

    def test_multiple_pages_keep_every_record(self):
        records = recalculate([trip(name=f"시험자{i}") for i in range(23)])["trips"]
        payload, info = render_pdf(records)
        self.assertEqual(info["count"], 23)
        with fitz.open(stream=payload, filetype="pdf") as doc:
            self.assertEqual(len(doc), 3)
            text = re.sub(r"\s+", "", "\n".join(p.get_text() for p in doc))
            for i in range(23):
                self.assertIn(f"시험자{i}", text)
            self.assertIn("460,000", text)

    def test_no_rows_is_an_error(self):
        with self.assertRaises(ValueError):
            render_pdf([trip(include=False)])

    def test_review_edits_cannot_bypass_daily_cap(self):
        records = recalculate([trip(department="가"), trip(department="나")])["trips"]
        records[1]["department"] = "가"
        _, info = render_pdf(records, rates_confirmed=True, data_confirmed=True)
        self.assertTrue(info["is_draft"])
        self.assertTrue(any("상한" in w for w in info["warnings"]))

    def test_manual_money_does_not_finalize_unsupported_scope_or_days(self):
        for record in (trip(scope="관외", daily_allowance=20000, claim_amount=20000),
                       trip(end_date="2026-10-23", daily_allowance=20000, claim_amount=20000)):
            with self.subTest(scope=record["scope"], end=record["end_date"]):
                _, info = render_pdf([record], rates_confirmed=True, data_confirmed=True)
                self.assertTrue(info["is_draft"])

    def test_empty_expense_is_not_silently_converted_to_zero(self):
        records = recalculate([trip(meal="")])["trips"]
        self.assertIsNone(records[0]["meal"])
        self.assertIsNone(records[0]["claim_amount"])
        _, info = render_pdf(records, rates_confirmed=True, data_confirmed=True)
        self.assertTrue(info["is_draft"])
        self.assertTrue(any("식비 확인 필요" in w for w in info["warnings"]))


if __name__ == "__main__":
    unittest.main()
