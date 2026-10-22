"""Read the 18-column monthly travel report without inventing missing fields.

PDF text extraction is local. The optional image path uses the same local OCR
engine as the screenshot reader. Employee identifiers are never returned.
"""
from __future__ import annotations

import base64
import bisect
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


def _result(trips, warnings, filename, *, image=False):
    if not trips:
        raise ValueError("월별집계 표에서 출장 내역을 찾지 못했습니다. 소계·총계만 있는 빈 목록인지 확인해 주세요.")
    warnings.insert(0, "월별집계에는 결재상태가 없습니다. 결재완료로 자동 표시하지 않았으니 지급대상을 별도로 확인해 주세요.")
    warnings.insert(0, "일비·청구액은 인식 후 화면의 지급기준으로 다시 계산하므로 원본 금액과 다를 수 있습니다. 지급기준과 결과를 확인해 주세요.")
    if image:
        warnings.insert(0, "월별집계 캡처 OCR 결과입니다. 성명·실제 출장일시·차량·경비를 원본과 대조해 주세요.")
    return {"trips": trips, "warnings": list(dict.fromkeys(warnings)),
            "source_name": re.split(r"[/\\]", str(filename))[-1] or ("capture.png" if image else "report.pdf")}


def parse_pdf(pdf_bytes: bytes, filename: str = "report.pdf") -> dict:
    """Extract supported monthly-report PDF cells; no password bypass or upload."""
    if not pdf_bytes or len(pdf_bytes) > 20 * 1024 * 1024:
        raise ValueError("20MB 이하의 월별집계 PDF를 선택해 주세요.")
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError("PDF를 열 수 없습니다. 올바른 PDF 파일인지 확인해 주세요.") from exc
    try:
        if document.needs_pass:
            raise ValueError("암호가 걸린 PDF는 읽을 수 없습니다. 접근 권한이 있는 원본에서 허용된 방법으로 내보낸 PDF 또는 캡처를 사용해 주세요.")
        if not 1 <= len(document) <= 30:
            raise ValueError("PDF는 1~30쪽까지 처리할 수 있습니다.")
        trips, warnings = [], []
        for page in document:
            textpage = page.get_textpage()
            for table in page.find_tables().tables:
                if table.col_count != 18:
                    continue
                rows = [[page.get_textbox(fitz.Rect(cell), textpage=textpage) if cell is not None else "" for cell in row.cells]
                        for row in table.rows]
                if not rows or _header_score(rows[0]) < 7:
                    continue
                for cells in rows[1:]:
                    if not re.fullmatch(r"\s*\d{1,3}\s*", cells[0]):
                        continue
                    trip, notes = _parse_cells(cells, len(trips) + 1)
                    trips.append(trip)
                    warnings.extend(notes)
                    if len(trips) > 500:
                        raise ValueError("출장 내역은 500건까지 처리할 수 있습니다.")
        if not trips:
            raise ValueError("이 PDF에서 지원하는 18열 '출장 월별집계' 표를 찾지 못했습니다. 해당 목록의 PDF 또는 캡처를 넣어 주세요.")
        result = _result(trips, warnings, filename)
        scale = min(1.8, 1600 / max(document[0].rect.width, 1))
        preview = document[0].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")
        result["preview_data"] = base64.b64encode(preview).decode("ascii")
        result["preview_note"] = "PDF 첫 페이지 미리보기" + (f" · 전체 {len(document)}쪽" if len(document) > 1 else "")
        return result
    finally:
        document.close()


def _image_cells(words, xs, ys):
    if __package__:
        from .ocr_parser import _cell_text
    else:
        from ocr_parser import _cell_text
    rows = [[[] for _ in range(len(xs) - 1)] for _ in range(len(ys) - 1)]
    for word in words:
        col = bisect.bisect_right(xs, word.left + word.width / 2) - 1
        row = bisect.bisect_right(ys, word.top + word.height / 2) - 1
        if 0 <= row < len(rows) and 0 <= col < len(xs) - 1:
            rows[row][col].append(word)
    return [[_cell_text(cell) for cell in row] for row in rows]


def _recover_report_grid(pix):
    """Keep the edges of shaded header bands instead of discarding the band.

    Monthly PDFs have a dark gray header. A generic thin-line detector can
    discard both header borders when that whole band falls below its threshold.
    """
    if __package__:
        from .ocr_parser import _groups
    else:
        from ocr_parser import _groups
    gray = fitz.Pixmap(fitz.csGRAY,pix)
    raw,width,height=gray.samples,gray.width,gray.height
    best = None
    for threshold in (200,205,215,225):
        dark=raw.translate(bytes(1 if i<threshold else 0 for i in range(256)))
        groups=_groups([y for y in range(height) if dark[y*width:(y+1)*width].count(1)>=width*.68])
        edges=[]
        for group in groups:
            if len(group)>max(4,height//150):
                edges.extend((float(group[0]),float(group[-1])))
            else:
                edges.append(sum(group)/len(group))
        if len(edges)<4:
            continue
        gaps=[b-a for a,b in zip(edges,edges[1:]) if b-a>3]
        median=sorted(gaps)[len(gaps)//2]
        # Retain a regular table block, not a remote footer rule.
        stop=next((i+1 for i,(a,b) in enumerate(zip(edges,edges[1:])) if i>=2 and b-a>median*2.4),len(edges))
        edges=edges[:stop]
        if len(edges)<4:
            continue
        scores=[0]*width
        for y in range(int(edges[0]),int(edges[-1])+1):
            scores=[a+b for a,b in zip(scores,dark[y*width:(y+1)*width])]
        count=int(edges[-1])-int(edges[0])+1
        xgroups=[g for g in _groups([x for x,s in enumerate(scores) if s>=count*.72]) if len(g)<=max(5,width//150)]
        xs=[sum(g)/len(g) for g in xgroups]
        if len(xs)==19 and (best is None or len(edges)>len(best[1])):
            best=(xs,edges)
    return best


def try_parse_monthly_image(pix, xs, ys, words, filename="capture.png"):
    """Return None for other forms; accept only an unambiguous monthly layout."""
    if len(xs) < 19 or len(ys) < 3:
        return None
    recovered_grid = _recover_report_grid(pix)
    if recovered_grid and (recovered_grid[1][0] < ys[0]-3 or len(recovered_grid[1])>len(ys)):
        xs,ys = recovered_grid
    physical = _image_cells(words, xs, ys)
    candidates = []
    for offset in range(len(xs) - 18):
        headers = [" ".join(row[offset + col] for row in physical[:2]) for col in range(18)]
        score = _header_score(headers)
        # Same-cell dates and times distinguish report actual periods from the
        # UI's separate actual-time and requested-period columns.
        body = [i for i, row in enumerate(physical)
                if re.fullmatch(r"\s*\d{1,3}\s*", row[offset])
                and len(_dates(row[offset + 3])) >= 2 and len(_times(row[offset + 3])) == 2]
        strong = []
        for index in body:
            row = physical[index][offset:offset + 18]
            valid_name = bool(re.search(r"[가-힣A-Za-z]{2}", _name(row[2])))
            scope = _compact(row[5]) in ("근무지내", "근무지외", "관내", "관외")
            car = _compact(row[6]).lower() in ("사용", "미사용", "이용", "미이용", "y", "n", "o", "x")
            duration = bool(re.search(r"\d+\s*(?:시간|분|일)", row[4]))
            money_count = sum(_amount(c) is not None for c in row[10:])
            if valid_name and scope and car and duration and money_count >= 5:
                strong.append(index)
        # Labels are evidence, not a gate that rejects a whole readable body.
        # Header recovery still requires several independent travel-only fields.
        normal = score >= 6 and body
        recovered = score >= 2 and len(strong) >= min(2, len(body)) and len(strong) >= len(body) * .6 and body
        if normal or recovered:
            candidates.append((score + len(strong) / max(1,len(body)), offset, min(body)))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        raise ValueError("월별집계 표의 열 배치가 여러 가지로 읽힙니다. 표 부분을 선명하게 잘라 다시 넣어 주세요.")
    _, offset, first = candidates[0]
    trips, warnings = [], []
    for physical_row in physical[first:]:
        cells = physical_row[offset:offset + 18]
        if not re.fullmatch(r"\s*\d{1,3}\s*", cells[0]):
            continue
        trip, notes = _parse_cells(cells, len(trips) + 1)
        trips.append(trip)
        warnings.extend(notes)
    if candidates[0][0] < 6:
        warnings.append("월별집계의 일부 열 제목이 불분명해 표선과 출장일시·시간·차량·금액 열을 함께 확인하여 배치했습니다. 원본과 열 배치를 대조해 주세요.")
    return _result(trips, warnings, filename, image=True)
