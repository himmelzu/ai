"""Extract supported personal and monthly travel-list text PDFs locally.

Personal exports contain separate requested and confirmed rows: only confirmed
rows supply actual times and approval status. Employee identifiers are omitted.
"""
from __future__ import annotations

import base64
from datetime import datetime
import re

import fitz

_DATE = re.compile(r"(?<!\d)(20\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)")
_TIME = re.compile(r"(?<!\d)([0-2]?\d)\s*[:：]\s*([0-5]\d)(?!\d)")
_HEADERS = {0: ("순번",), 1: ("소속직급",), 2: ("성명",), 3: ("출장기간",),
            4: ("총출장시간",), 5: ("구분",), 6: ("공무용차량", "공용차량"),
            7: ("출장지",), 8: ("출장목적",), 10: ("일비",), 17: ("청구액",)}


def _compact(value):
    return re.sub(r"[^가-힣A-Za-z0-9]", "", str(value))


def _clean(value):
    return str(value or "").replace("\u00a0", " ").strip()


def _unwrap(value):
    return _clean(value).replace("\r", "").replace("\n", "")


def _dates(value):
    value = value.replace("‑", "-").replace("–", "-").replace("—", "-")
    found = []
    for match in _DATE.finditer(value):
        try:
            found.append(datetime(*map(int, match.groups())).date().isoformat())
        except ValueError:
            continue
    return found


def _times(value):
    return [f"{int(m[1]):02d}:{m[2]}" for m in _TIME.finditer(value) if int(m[1]) < 24]


def _period(value):
    halves = re.split(r"[~～∼]", value, maxsplit=1)
    if len(halves) == 2:
        start_dates, end_dates = _dates(halves[0]), _dates(halves[1])
        start_times, end_times = _times(halves[0]), _times(halves[1])
        return (start_dates[0] if len(start_dates) == 1 else "",
                end_dates[0] if len(end_dates) == 1 else "",
                start_times[0] if len(start_times) == 1 else "",
                end_times[0] if len(end_times) == 1 else "")
    dates, times = _dates(value), _times(value)
    if len(dates) == 2 and len(times) == 2:
        return dates[0], dates[1], times[0], times[1]
    # Without a separator, a lone surviving value may be the start or the end.
    # Keep it unassigned instead of silently moving an end time to the start.
    return "", "", "", ""


def _department_rank(value):
    text = _clean(value)
    for delimiter in ("\r", "/"):
        if delimiter in text:
            department, rank = text.split(delimiter, 1)
            return _unwrap(department), _unwrap(rank)
    plain = _unwrap(text)
    # A clear rank prefix can separate a line-wrapped PDF cell. Otherwise leave
    # the rank empty for review rather than guessing the department's boundary.
    match = re.search(r"지방[가-힣]*(?:지도사|연구사|지도관|연구관|서기보|서기|주사보|주사|사무관|서기관|사|원)$", plain)
    if match and match.start() > 0:
        return plain[:match.start()], plain[match.start():]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) == 2 and re.fullmatch(r"(?:주무관|사무관|서기관|공무직|기간제|연구원)", lines[1]):
        return lines[0], lines[1]
    return plain, ""


def _name(value):
    text = _clean(value)
    text = re.split(r"[（(]", text, 1)[0]
    # Identifiers sometimes wrap onto the next line without readable brackets.
    text = re.split(r"[A-Za-z]*\d", text, 1)[0]
    return re.sub(r"\s+", "", text)


def _amount(value):
    text = re.sub(r"[\s,원₩]", "", _clean(value))
    if text in ("-", "–", "—"):
        return 0
    if re.fullmatch(r"\d{1,9}", text):
        number = int(text)
        return number if number <= 100000000 else None
    return None


def _header_score(cells):
    if len(cells) != 18:
        return 0
    return sum(any(label in _compact(cells[index]) for label in labels)
               for index, labels in _HEADERS.items())


def _parse_cells(cells, ordinal):
    if len(cells) != 18:
        raise ValueError("월별집계 표의 열 개수가 예상과 다릅니다.")
    notes = []
    department, rank = _department_rank(cells[1])
    name = _name(cells[2])
    start, end, start_time, end_time = _period(cells[3])
    if not all((start, end, start_time, end_time)):
        notes.append(f"{ordinal}행: 실제 출장 시작·종료 일시를 모두 읽지 못했습니다. 원본과 대조해 주세요.")
    if not rank:
        notes.append(f"{ordinal}행: 소속과 직급을 명확히 나누지 못했습니다. 직급을 확인해 주세요.")
    if not name:
        notes.append(f"{ordinal}행: 성명을 읽지 못했습니다.")
    scope_text = _compact(cells[5])
    scope = "관내" if scope_text in ("근무지내", "관내") else "관외" if scope_text in ("근무지외", "관외") else ""
    vehicle_text = _compact(cells[6]).lower()
    vehicle = "unused" if vehicle_text in ("미사용", "미이용", "사용안함", "n", "x") else "used" if vehicle_text in ("사용", "이용", "y", "o") else "unknown"
    if not scope:
        notes.append(f"{ordinal}행: 관내·관외 구분을 확인해 주세요.")
    if vehicle == "unknown":
        notes.append(f"{ordinal}행: 공무용차량 사용 여부를 확인해 주세요.")
    values = [_amount(cells[i]) for i in range(10, 18)]
    transport = None if values[3] is None or values[4] is None else values[3] + values[4]
    if values[4]:
        notes.append(f"{ordinal}행: 원본 교통비와 현지교통비를 출력 교통비에 합산했습니다.")
    if any(v is None for v in values):
        notes.append(f"{ordinal}행: 원본의 경비 금액 중 미인식 값이 있습니다. 빈 금액을 0원으로 대신하지 않았습니다.")
    supported = scope == "관내" and start and end and start == end
    if not supported:
        notes.append(f"{ordinal}행: 관외·여러 날 출장 또는 날짜 미확인 내역은 기본 청구 대상에서 제외했습니다. 필요 시 별도 검토 후 포함하세요.")
    trip = {
        "department": department, "rank": rank, "name": name,
        "start_date": start, "end_date": end,
        "start_time": start_time, "end_time": end_time,
        "scope": scope, "vehicle": vehicle, "destination": _unwrap(cells[7]), "purpose": _unwrap(cells[8]),
        "daily_allowance": values[0], "meal": values[1], "lodging": values[2],
        "transport": transport, "other": values[5], "claim_amount": values[7],
        "approval_status": "", "include": bool(supported), "ocr_warnings": notes,
    }
    return trip, notes


_PERSONAL_HEADERS = {0: "순번", 1: "구분", 3: "출발일자", 4: "도착일자",
                     5: "총출장시간", 6: "출장지", 7: "차량", 8: "출장목적",
                     9: "소속", 10: "출장자", 14: "결재상태", 15: "여비등급"}


def _personal_header_score(cells):
    if len(cells) != 18:
        return 0
    return sum(label in _compact(cells[index]) for index, label in _PERSONAL_HEADERS.items())


def _point(value):
    dates, times = _dates(value), _times(value)
    return (dates[0] if len(dates) == 1 else "", times[0] if len(times) == 1 else "")


def _personal_row(cells):
    return (len(cells) == 18 and re.fullmatch(r"\d{1,4}", _clean(cells[0]))
            and _compact(cells[2]) in ("신청", "확인"))


def _personal_trip(cells, ordinal, requested=None, *, confirmed=True):
    notes = []
    department = _unwrap(cells[9])
    name = _name(cells[10])
    # This export has no rank column. Never infer one from department or name.
    rank = ""
    if confirmed:
        start, start_time = _point(cells[3])
        end, end_time = _point(cells[4])
    else:
        start = end = start_time = end_time = ""
    scope_text = _compact(cells[1])
    scope = "관내" if scope_text in ("근무지내", "관내") else "관외" if scope_text in ("근무지외", "관외") else ""
    car = _compact(cells[7]).lower()
    vehicle = ("unused" if car in ("미사용", "미이용", "사용안함", "n", "x") else
               "used" if car in ("사용", "이용", "y", "o") else "unknown") if confirmed else "unknown"
    status = _compact(cells[14]) if confirmed else "확인내역없음"
    # The user's old monthly export had no status column. This personal export
    # does: its actual confirmation status overrides the requested row's status.
    approved = status == "결재완료"
    supported = bool(confirmed and approved and scope == "관내" and start and end and start == end)
    if not confirmed:
        notes.append(f"{ordinal}행: 신청 내역만 있고 확인 내역이 없어 청구 대상에서 제외했습니다. 신청시각을 실제시각으로 대신하지 않았습니다.")
    elif not approved:
        notes.append(f"{ordinal}행: 확인 내역의 결재상태가 결재완료가 아니어서 청구 대상에서 제외했습니다.")
    if confirmed and not all((start, end, start_time, end_time)):
        notes.append(f"{ordinal}행: 확인 내역의 실제 출발·도착 일시가 불완전합니다. 원본과 대조해 주세요.")
    if confirmed and (scope != "관내" or not start or not end or start != end):
        notes.append(f"{ordinal}행: 관외·여러 날 출장 또는 날짜 미확인 내역은 기본 청구 대상에서 제외했습니다.")
    if confirmed and vehicle == "unknown":
        notes.append(f"{ordinal}행: 확인 내역의 공용차량 사용 여부를 확인해 주세요.")
    if not department or not name:
        notes.append(f"{ordinal}행: 소속 또는 성명이 비어 있습니다. 원본과 대조해 주세요.")
    original_expenditure, original_allowance = _amount(cells[11]), _amount(cells[12])
    # This report gives no separate meal/lodging/transport/other expense cells.
    # For a zero-valued source, initialize the new local daily-allowance claim's
    # additional expenses to zero. Nonzero/unknown source sums need item review.
    zero_expenses = original_expenditure == 0 and original_allowance == 0
    expenses = 0 if zero_expenses else None
    if not zero_expenses:
        notes.append(f"{ordinal}행: 원본 지출·여비에 금액이 있거나 읽지 못한 값이 있습니다. 세부 경비를 확인해 입력해 주세요.")
    trip = {
        "department": department, "rank": rank, "name": name,
        "start_date": start, "end_date": end, "start_time": start_time, "end_time": end_time,
        "scope": scope, "vehicle": vehicle, "destination": _unwrap(cells[6]), "purpose": _unwrap(cells[8]),
        "daily_allowance": None, "meal": expenses, "lodging": expenses,
        "transport": expenses, "other": expenses, "claim_amount": None,
        "approval_status": status, "include": supported, "ocr_warnings": notes,
    }
    if requested is None and not confirmed:
        requested = cells
    if requested is not None:
        requested_start, requested_start_time = _point(requested[3])
        requested_end, requested_end_time = _point(requested[4])
        trip.update(requested_start_date=requested_start, requested_end_date=requested_end,
                    requested_start_time=requested_start_time, requested_end_time=requested_end_time)
    return trip, notes


def _personal_trips(rows):
    trips, warnings = [], []
    pending = None
    previous_number = None
    confirmations = application_only = 0

    def append(cells, requested=None, confirmed=True):
        trip, notes = _personal_trip(cells, len(trips) + 1, requested, confirmed=confirmed)
        trips.append(trip)
        warnings.extend(notes)

    for cells in rows:
        number = int(_clean(cells[0]))
        if previous_number is not None and number <= previous_number:
            raise ValueError("출장 내역 순번이 중복되거나 순서가 바뀌어 있습니다. 개인별 내역 PDF를 다시 내려받아 주세요.")
        previous_number = number
        kind = _compact(cells[2])
        if kind == "신청":
            if pending is not None:
                append(pending, confirmed=False)
                application_only += 1
            pending = cells
            continue
        matched = (pending is not None and int(_clean(pending[0])) + 1 == number
                   and _name(pending[10]) == _name(cells[10])
                   and _unwrap(pending[9]) == _unwrap(cells[9]))
        if pending is not None and not matched:
            append(pending, confirmed=False)
            application_only += 1
        append(cells, pending if matched else None)
        confirmations += 1
        pending = None
    if pending is not None:
        append(pending, confirmed=False)
        application_only += 1
    return trips, warnings, {"source_rows": len(rows), "trip_count": len(trips),
                             "confirmation_count": confirmations, "application_only_count": application_only,
                             "excluded_count": sum(not trip["include"] for trip in trips)}


def _result(trips, warnings, filename, input_format, source_summary):
    if not trips:
        raise ValueError("PDF 표에서 출장 내역을 찾지 못했습니다. 빈 목록인지 확인해 주세요.")
    personal = input_format == "personal_trip_list"
    if personal:
        warnings.insert(0, "신청·확인 두 줄을 한 출장으로 합쳤으며 실제 출발·도착 시각과 결재상태는 확인 내역에서 읽었습니다.")
        warnings.insert(0, "개인별 내역에는 직급이 없어 비워 두었습니다. 별도 경비는 원본 지출·여비가 0인 행에 한해 0원으로 시작하며 필요 시 추가할 수 있습니다.")
    else:
        warnings.insert(0, "월별집계 PDF는 시스템에서 결재완료된 출장만 내려받은 자료로 간주하여 결재상태를 결재완료로 적용했습니다.")
    warnings.insert(0, "일비·청구액은 화면의 지급기준으로 계산하므로 원본 금액과 다를 수 있습니다.")
    return {"trips": trips, "warnings": list(dict.fromkeys(warnings)), "input_kind": "pdf",
            "input_format": input_format, "source_summary": source_summary,
            "approval_policy": "source_confirmation_status" if personal else "source_system_approved_export",
            "source_name": re.split(r"[/\\]", str(filename))[-1] or "report.pdf"}


def _table_signature(table, page_width):
    for row in table.rows:
        if len(row.cells) == 18 and all(cell is not None for cell in row.cells):
            return [(cell[0] / page_width, cell[2] / page_width) for cell in row.cells]
    return None


def _same_table(left, right):
    return bool(left and right and len(left) == len(right)
                and all(abs(a - b) <= .005 for pair, other in zip(left, right) for a, b in zip(pair, other)))


def _travel_like_text(text, schema):
    """Identify uncollected travel data, not ordinary footer/date metadata."""
    if len(_dates(text)) < 2 or len(_times(text)) < 2:
        return False
    compact = _compact(text)
    if not any(label in compact for label in ("근무지내", "근무지외", "관내", "관외")):
        return False
    if schema == "personal_trip_list":
        return "신청" in compact or "확인" in compact
    return "사용" in compact or "이용" in compact


def _incomplete_table():
    return ValueError("다음 페이지 또는 일부 출장 표를 읽지 못했습니다. 내역 누락 방지를 위해 변환을 중단했습니다. 원본 PDF를 다시 내려받아 주세요.")


def parse_pdf(pdf_bytes: bytes, filename: str = "report.pdf") -> dict:
    """Extract supported text PDF tables without bypassing password protection."""
    if not pdf_bytes or len(pdf_bytes) > 20 * 1024 * 1024:
        raise ValueError("20MB 이하의 출장 내역 PDF를 선택해 주세요.")
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError("PDF를 열 수 없습니다. 올바른 PDF 파일인지 확인해 주세요.") from exc
    try:
        if document.needs_pass:
            raise ValueError("암호가 걸린 PDF는 읽을 수 없습니다. 권한이 있는 시스템에서 허용된 방법으로 내보낸 PDF를 사용해 주세요.")
        if not 1 <= len(document) <= 30:
            raise ValueError("PDF는 1~30쪽까지 처리할 수 있습니다.")
        schema = None
        signature = None
        personal_rows = []
        monthly_rows = []
        for page in document:
            textpage = page.get_textpage()
            rows_before = len(personal_rows) + len(monthly_rows)
            unsupported = []
            collected_regions = []
            for table in page.find_tables().tables:
                if table.col_count != 18:
                    unsupported.append(fitz.Rect(table.bbox))
                    continue
                rows = [[page.get_textbox(fitz.Rect(cell), textpage=textpage) if cell is not None else "" for cell in row.cells]
                        for row in table.rows]
                if not rows:
                    continue
                current_signature = _table_signature(table, page.rect.width)
                detected = ("personal_trip_list" if _personal_header_score(rows[0]) >= 9 else
                            "monthly_report" if _header_score(rows[0]) >= 7 else None)
                if detected is not None:
                    if schema is not None and schema != detected:
                        raise ValueError("서로 다른 출장 표 형식이 한 PDF에 섞여 있습니다. 한 종류의 내역만 내려받아 주세요.")
                    schema, signature = detected, current_signature
                    body = rows[1:]
                elif schema is not None and _same_table(signature, current_signature):
                    body = rows
                else:
                    unsupported.append(fitz.Rect(table.bbox))
                    continue
                table_rows_before = len(personal_rows) + len(monthly_rows)
                for cells in body:
                    if schema == "personal_trip_list":
                        if _personal_row(cells):
                            personal_rows.append(cells)
                        elif re.fullmatch(r"\s*\d{1,4}\s*", cells[0]):
                            raise ValueError("개인별 내역의 신청·확인 구분을 읽을 수 없습니다. 원본 PDF를 확인해 주세요.")
                        elif _travel_like_text(" ".join(cells), schema):
                            raise _incomplete_table()
                    elif re.fullmatch(r"\s*\d{1,3}\s*", cells[0]):
                        monthly_rows.append(cells)
                    elif _travel_like_text(" ".join(cells), schema):
                        raise _incomplete_table()
                    if len(personal_rows) > 1000 or len(monthly_rows) > 500:
                        raise ValueError("출장 내역은 500건까지 처리할 수 있습니다.")
                if len(personal_rows) + len(monthly_rows) > table_rows_before:
                    collected_regions.append(fitz.Rect(table.bbox))
            if schema is not None:
                for region in unsupported:
                    # Some table detectors return a fragment inside a complete
                    # table too. Its data has already been collected above.
                    if any(collected.contains(region) for collected in collected_regions):
                        continue
                    if _travel_like_text(page.get_textbox(region, textpage=textpage), schema):
                        raise _incomplete_table()
                if len(personal_rows) + len(monthly_rows) == rows_before:
                    if _travel_like_text(page.get_text(textpage=textpage), schema):
                        raise _incomplete_table()
        if schema == "personal_trip_list":
            trips, warnings, summary = _personal_trips(personal_rows)
        elif schema == "monthly_report":
            trips, warnings = [], []
            for cells in monthly_rows:
                trip, notes = _parse_cells(cells, len(trips) + 1)
                trip["approval_status"] = "결재완료"
                trips.append(trip)
                warnings.extend(notes)
            summary = {"source_rows": len(monthly_rows), "trip_count": len(trips),
                       "confirmation_count": len(trips), "application_only_count": 0,
                       "excluded_count": sum(not trip["include"] for trip in trips)}
        else:
            raise ValueError("지원하는 개인별 출장 내역 또는 출장 월별집계 표를 찾지 못했습니다. 시스템에서 내려받은 원본 PDF를 넣어 주세요.")
        if len(trips) > 500:
            raise ValueError("출장 내역은 500건까지 처리할 수 있습니다.")
        result = _result(trips, warnings, filename, schema, summary)
        scale = min(1.8, 1600 / max(document[0].rect.width, 1))
        preview = document[0].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")
        result["preview_data"] = base64.b64encode(preview).decode("ascii")
        result["preview_note"] = "PDF 첫 페이지 미리보기" + (f" · 전체 {len(document)}쪽" if len(document) > 1 else "")
        return result
    finally:
        document.close()
