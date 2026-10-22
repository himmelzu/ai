"""Reviewed travel records -> landscape A4 statement. No network access."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import math
import re

import fitz

DEFAULT_RATES = dict(rate_long=20000, rate_short=10000, vehicle_deduction=10000,
                     threshold_minutes=240, daily_cap=20000)
MONEY_KEYS = ("daily_allowance", "meal", "lodging", "transport", "other")
TEXT_KEYS = ("department", "rank", "name", "start_date", "end_date", "start_time",
             "end_time", "scope", "vehicle", "destination", "purpose", "approval_status",
             "requested_start_date", "requested_end_date", "requested_start_time", "requested_end_time")


def money(value, *, optional=False):
    if optional and (value is None or value == ""):
        return None
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise ValueError("금액은 0 이상의 정수로 입력해 주세요.")
    if isinstance(value, str):
        value = value.replace(",", "").strip()
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise ValueError("금액은 0 이상의 정수로 입력해 주세요.") from None
    if not math.isfinite(numeric) or not numeric.is_integer() or not 0 <= numeric <= 100000000:
        raise ValueError("금액은 0~100,000,000 사이의 정수로 입력해 주세요.")
    return int(numeric)


def validate_rates(values=None):
    rates = DEFAULT_RATES.copy()
    if values:
        if not isinstance(values, dict):
            raise ValueError("지급기준 형식이 잘못되었습니다.")
        rates.update({k: money(v) for k, v in values.items() if k in rates})
    if not 1 <= rates["threshold_minutes"] <= 1440:
        raise ValueError("시간 기준은 1~1,440분으로 입력해 주세요.")
    return rates


def normalize_trip(raw):
    if not isinstance(raw, dict):
        raise ValueError("출장 내역 형식이 잘못되었습니다.")
    trip = {k: str(raw.get(k) or "").strip()[:1000] for k in TEXT_KEYS}
    trip["include"] = raw.get("include", True) is not False
    trip["vehicle"] = trip["vehicle"] if trip["vehicle"] in ("used", "unused") else "unknown"
    for key in MONEY_KEYS:
        trip[key] = money(raw.get(key), optional=True)
    trip["claim_amount"] = money(raw.get("claim_amount"), optional=True)
    # Keep OCR provenance for on-screen review; never interpret it as instructions.
    trip["ocr_warnings"] = [str(w)[:300] for w in raw.get("ocr_warnings", [])][:20]
    return trip


def duration_minutes(trip):
    try:
        start = datetime.strptime(trip["start_date"] + " " + trip["start_time"], "%Y-%m-%d %H:%M")
        end = datetime.strptime(trip["end_date"] + " " + trip["end_time"], "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return None
    minutes = int((end - start).total_seconds() // 60)
    return minutes if 0 < minutes <= 31 * 1440 else None


def cost_total(trip):
    return None if any(trip[k] is None for k in MONEY_KEYS) else sum(trip[k] for k in MONEY_KEYS)


def trip_issues(trip):
    issues = []
    for key, label in (("name", "성명"), ("department", "부서"), ("rank", "직급"),
                       ("destination", "출장지"), ("purpose", "출장목적")):
        if not trip[key]:
            issues.append(label + " 누락")
    if duration_minutes(trip) is None:
        issues.append("실제 시작·종료 일시 확인 필요")
    if trip["vehicle"] == "unknown":
        issues.append("공용차량 확인 필요")
    if trip["scope"] not in ("관내", "관외"):
        issues.append("출장 구분 확인 필요")
    elif trip["scope"] == "관외":
        issues.append("관외 지급기준 별도 검토 필요")
    if trip["start_date"] and trip["end_date"] and trip["start_date"] != trip["end_date"]:
        issues.append("여러 날 출장 지급기준 별도 검토 필요")
    if trip["approval_status"] != "결재완료":
        issues.append("결재상태 확인 필요")
    for key, label in zip(MONEY_KEYS, ("일비", "식비", "숙박비", "교통비", "기타")):
        if trip[key] is None:
            issues.append(label + " 확인 필요")
    return issues


def recalculate(raw_trips, rate_values=None):
    if not isinstance(raw_trips, list) or not 1 <= len(raw_trips) <= 500:
        raise ValueError("출장 내역은 1~500건까지 처리할 수 있습니다.")
    rates = validate_rates(rate_values)
    trips = [normalize_trip(t) for t in raw_trips]
    warnings = list(dict.fromkeys(w for t in trips if t["include"] for w in t["ocr_warnings"]))
    daily_paid = {}
    for index, trip in enumerate(trips, 1):
        minutes = duration_minutes(trip)
        if not trip["include"]:
            continue
        if (trip["scope"] != "관내" or trip["start_date"] != trip["end_date"]
                or minutes is None or trip["vehicle"] == "unknown"):
            trip["daily_allowance"] = None
            trip["claim_amount"] = None
            warnings.append(f"{index}행: 관외·여러 날 출장 또는 필수정보 누락으로 일비를 자동 계산하지 않았습니다.")
            continue
        base = rates["rate_long"] if minutes >= rates["threshold_minutes"] else rates["rate_short"]
        proposed = max(0, base - (rates["vehicle_deduction"] if trip["vehicle"] == "used" else 0))
        if not trip["name"] or not trip["department"]:
            trip["daily_allowance"] = None
            trip["claim_amount"] = None
            warnings.append(f"{index}행: 성명·부서가 없어 개인별 일일 상한을 확인할 수 없습니다.")
            continue
        person_day = (trip["department"], trip["name"], trip["start_date"])
        already = daily_paid.get(person_day, 0)
        amount = min(proposed, max(0, rates["daily_cap"] - already))
        if amount != proposed:
            warnings.append(f"{index}행: 같은 직원의 같은 날 일비 상한을 적용했습니다.")
        daily_paid[person_day] = already + amount
        trip["daily_allowance"] = amount
        trip["claim_amount"] = cost_total(trip)
    return {"trips": trips, "warnings": warnings, "rates": rates}


def _wrap(text, font, size, width):
    lines = []
    for paragraph in str(text).split("\n"):
        line = ""
        for char in paragraph:
            if line and font.text_length(line + char, fontsize=size) > width:
                lines.append(line)
                line = ""
            line += char
        lines.append(line)
    return lines or [""]


def render_pdf(raw_trips, *, rates=None, rates_confirmed=False, data_confirmed=False,
               officer_title="", officer_name="", source_name="", **ignored):
    active_rates = validate_rates(rates)
    if not isinstance(raw_trips, list) or not 1 <= len(raw_trips) <= 500:
        raise ValueError("출장 내역은 1~500건까지 처리할 수 있습니다.")
    trips = [normalize_trip(t) for t in raw_trips]
    selected = [t for t in trips if t["include"]]
    if not selected:
        raise ValueError("PDF에 포함할 출장 내역을 한 건 이상 선택해 주세요.")
    warnings = []
    for i, trip in enumerate(selected, 1):
        issues = trip_issues(trip)
        if issues:
            warnings.append(f"{i}행: " + ", ".join(issues))
        total = cost_total(trip)
        if trip["claim_amount"] is None:
            trip["claim_amount"] = total
        elif total is not None and trip["claim_amount"] > total:
            warnings.append(f"{i}행: 청구액이 경비 합계보다 큽니다.")
    # Review edits can merge identities or change dates after a calculation.
    # Validate the submitted values independently of the browser's dirty flag.
    paid_by_day = {}
    for trip in selected:
        if trip["scope"] != "관내" or trip["daily_allowance"] is None:
            continue
        key = (trip["department"], trip["name"], trip["start_date"])
        paid_by_day[key] = paid_by_day.get(key, 0) + trip["daily_allowance"]
    if any(value > active_rates["daily_cap"] for value in paid_by_day.values()):
        warnings.append("동일 직원·동일 날짜의 일비 합계가 설정한 일일 상한을 넘습니다. 일비를 다시 계산해 주세요.")
    if not rates_confirmed:
        warnings.append("기관 지급기준을 확인하지 않았습니다. 금액은 예시 기준으로 계산된 검토용입니다.")
    if not data_confirmed:
        warnings.append("OCR 인식 내용 및 지급대상 확인이 완료되지 않았습니다.")
    draft = bool(warnings)
    fontpath = Path(__file__).parent / "fonts" / "NotoSansCJKkr-Regular.otf"
    font = fitz.Font(fontfile=str(fontpath))
    document = fitz.open()
    width, height = 841.89, 595.28
    left, right = 28, width - 28
    raw_widths = [22, 68, 35, 107, 54, 27, 42, 64, 132, 45, 31, 31, 31, 31, 48, 51, 56]
    scale = (right-left) / sum(raw_widths)
    column_widths = [v*scale for v in raw_widths]
    xs = [left]
    for colwidth in column_widths:
        xs.append(xs[-1]+colwidth)
    headers = ["순번", "소속/직급", "성명", "출장기간", "총출장시간", "구분", "공용차량\n사용여부",
               "출장지", "출장목적", "일비", "식비", "숙박비", "교통비", "기타", "합계", "청구액\n(수령액)", "영수인"]
    rows = []
    for index, trip in enumerate(selected, 1):
        duration = duration_minutes(trip)
        duration_text = f"{duration//60}시간{duration%60}분" if duration is not None else "확인 필요"
        start = (trip["start_date"] + " " + trip["start_time"]).strip() or "확인 필요"
        end = (trip["end_date"] + " " + trip["end_time"]).strip() or "확인 필요"
        total = cost_total(trip)
        fmt = lambda v: "확인 필요" if v is None else (f"{v:,}" if v else "-")
        cells = [str(index), trip["department"] + "/\n" + trip["rank"], trip["name"], start + "\n~ " + end,
                 duration_text, trip["scope"], {"used":"사용", "unused":"미사용"}.get(trip["vehicle"], "확인 필요"),
                 trip["destination"], trip["purpose"], *[fmt(trip[k]) for k in MONEY_KEYS],
                 fmt(total), fmt(trip["claim_amount"]), ""]
        maxlines = max(len(_wrap(c, font, 7.0, w-6)) for c,w in zip(cells,column_widths))
        rowheight = max(31, maxlines*9+7)
        if rowheight > 250:
            raise ValueError(f"{index}행의 글이 너무 깁니다. 내용을 줄여 주세요.")
        rows.append((cells,rowheight,trip))
    batches = []
    current, used = [], 0
    for row in rows:
        if current and (used+row[1] > 365 or len(current) >= 10):
            batches.append(current)
            current, used = [], 0
        current.append(row)
        used += row[1]
    if current:
        batches.append(current)

    def text_box(page, rect, text, size=7.0, align="center", color=(0.12,0.15,0.18)):
        lines = _wrap(text,font,size,rect.width-6)
        lineheight = size*1.28
        top = rect.y0 + max(2,(rect.height-len(lines)*lineheight)/2)
        for i,line in enumerate(lines):
            length = font.text_length(line,fontsize=size)
            x = rect.x0+3 if align=="left" else rect.x1-3-length if align=="right" else rect.x0+(rect.width-length)/2
            page.insert_text((x,top+size+i*lineheight),line,fontname="kr",fontsize=size,color=color)

    for page_number,batch in enumerate(batches,1):
        page=document.new_page(width=width,height=height)
        page.insert_font(fontname="kr",fontfile=str(fontpath))
        text_box(page,fitz.Rect(left,23,right,53),"여 비 지 급 명 세 서",19)
        label="검토용 · OCR 인식 내용 및 지급기준 확인 필요" if draft else "확인된 출장 내역"
        text_box(page,fitz.Rect(left,55,right,72),label,8,color=(0.65,0.25,0.06) if draft else (0.18,0.33,0.3))
        y=79
        header_bottom=y+33
        for i,header in enumerate(headers):
            rect=fitz.Rect(xs[i],y,xs[i+1],header_bottom)
            page.draw_rect(rect,color=(0.45,0.47,0.46),fill=(1,1,0.68),width=0.45)
            text_box(page,rect,header,7)
        y=header_bottom
        for cells,rowheight,trip in batch:
            for i,cell in enumerate(cells):
                rect=fitz.Rect(xs[i],y,xs[i+1],y+rowheight)
                page.draw_rect(rect,color=(0.68,0.7,0.69),width=0.35)
                text_box(page,rect,cell,7,align="left" if i in (7,8) else "right" if 9<=i<=15 else "center")
            y+=rowheight
        totals=[sum(row[2][k] or 0 for row in batch) for k in MONEY_KEYS]
        unknown_columns = [any(row[2][k] is None for row in batch) for k in MONEY_KEYS]
        has_missing=any(unknown_columns)
        page.draw_rect(fitz.Rect(left,y,right,y+27),color=(0.4,0.44,0.43),fill=(0.95,0.97,0.95),width=0.5)
        text_box(page,fitz.Rect(left,y,xs[6],y+27),"소          계" if len(batches)==1 else "페이지 소계",9)
        text_box(page,fitz.Rect(xs[6],y,xs[9],y+27),f"{len(batch)}건",8)
        amount_values=totals+[sum(totals),sum(row[2]["claim_amount"] or 0 for row in batch)]
        for i,value in enumerate(amount_values,9):
            unknown = unknown_columns[i-9] if i<=13 else has_missing
            text_box(page,fitz.Rect(xs[i],y,xs[i+1],y+27),"확인 필요" if unknown else (f"{value:,}" if value else "-"),7,align="right")
        names={row[2]["name"] for row in batch}
        if len(names)==1:
            text_box(page,fitz.Rect(xs[16],y,right,y+27),next(iter(names))+" (인)",7)
        y+=27
        # A blank confirmation/signature line; the program never certifies a match itself.
        confirmation="출장명령부(신고서) 대조 확인: " + str(officer_title)[:40] + "  " + str(officer_name)[:20] + "  __________________ (인)"
        page.draw_rect(fitz.Rect(left,y,right,y+26),color=(0.4,0.44,0.43),width=0.5)
        text_box(page,fitz.Rect(left,y,right,y+26),confirmation,9)
        if len(batches)>1 and page_number==len(batches):
            all_total=sum(t[k] or 0 for t in selected for k in MONEY_KEYS)
            missing=any(cost_total(t) is None for t in selected)
            text_box(page,fitz.Rect(left,y+29,right,y+46),f"전체 {len(selected)}건 · 전체 경비 합계: "+("확인 필요" if missing else f"{all_total:,}원"),9)
        text_box(page,fitz.Rect(left,height-38,right,height-23),f"{page_number} / {len(batches)}   ·   캡처 인식 후 확인하여 작성",7,color=(0.4,0.44,0.43))
    document.set_metadata({"title":"여비지급명세서"+(" (검토용)" if draft else ""),"producer":"여비청구서 변환기"})
    # The CFF Korean font needs fontTools subsetting; the full typeface would add
    # roughly 13 MB to every statement even when only a few glyphs are used.
    document.subset_fonts(fallback=True)
    payload=document.tobytes(garbage=4,deflate=True)
    document.close()
    return payload,{"is_draft":draft,"warnings":warnings,"count":len(selected)}
