"""Local OCR for the 근무지내출장처리 list screenshot.

The actual check-in/check-out column supplies times.  The requested period
supplies dates only: its planned times must never become actual travel times.
Employee identifiers are deliberately excluded from the returned data.
"""
from __future__ import annotations

import bisect
from collections import Counter, defaultdict
import csv
from datetime import date
import io
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import unicodedata
from typing import NamedTuple

import fitz


class Word(NamedTuple):
    text: str
    left: float
    top: float
    width: float
    height: float
    confidence: float


class TableLayout(NamedTuple):
    xs: list[float]
    first_data_row: int
    recovered: bool


_COLUMNS = (
    "number", "department", "rank", "name", "identifier", "actual",
    "requested", "destination", "purpose", "grade", "vehicle", "approval",
)
_DATE = re.compile(r"(?<!\d)(20\d{2})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})(?!\d)")
_TIME = re.compile(r"(?<!\d)([0-2]?\d)\s*[:：]\s*([0-5]\d)(?!\d)")


def _groups(numbers: list[int], max_gap: int = 1) -> list[list[int]]:
    groups: list[list[int]] = []
    for number in numbers:
        if not groups or number - groups[-1][-1] > max_gap:
            groups.append([number])
        else:
            groups[-1].append(number)
    return groups


def _grid(pix: fitz.Pixmap) -> tuple[list[float], list[float]]:
    """Locate thin, near-full-width row rules and column rules without Pillow."""
    gray = fitz.Pixmap(fitz.csGRAY, pix)
    raw, width, height = gray.samples, gray.width, gray.height
    best: tuple[list[float], list[float]] = ([], [])
    # JPEG compression and browser downscaling can leave otherwise continuous
    # pale grid rules just above 245; white page backgrounds remain 255.
    for threshold in (190, 225, 245, 250, 253):
        dark = raw.translate(bytes(1 if i < threshold else 0 for i in range(256)))
        coverage = [dark[y * width:(y + 1) * width].count(1) for y in range(height)]
        horizontal = [y for y, score in enumerate(coverage) if score >= width * 0.68]
        all_groups = _groups(horizontal)
        y_groups = [g for g in all_groups if len(g) <= max(4, height // 150)]
        if len(y_groups) < 3:
            continue
        centers = [sum(g) / len(g) for g in y_groups]
        row_gap = statistics.median(b - a for a, b in zip(centers, centers[1:]))
        # JPEG ringing may add another near-full-width stripe a few pixels
        # beside a real rule. Merge these halos and keep the strongest rule,
        # rather than treating them as extra trip rows.
        merged: list[list[int]] = []
        for group in y_groups:
            if merged and group[0] - merged[-1][-1] <= max(4, row_gap * 0.25):
                merged[-1].extend(group)
            else:
                merged.append(list(group))
        ys = []
        for group in merged:
            maximum = max(coverage[y] for y in group)
            strongest = [y for y in group if coverage[y] == maximum]
            ys.append(sum(strongest) / len(strongest))
        # A tinted two-tier header becomes one thick band at lighter
        # thresholds. Its top/bottom edges remain table boundaries. Retaining
        # those edges lets pale or downscaled body rules use that threshold
        # without silently discarding the header and the first trip.
        for group in all_groups:
            if max(4, height // 150) < len(group) <= row_gap * 3:
                ys.extend((float(group[0]), float(group[-1])))
        ys = sorted(set(ys))
        top, bottom = int(ys[0]), int(ys[-1])
        step = max(1, (bottom - top) // 700)
        scores = [0] * width
        samples = 0
        for y in range(top, bottom + 1, step):
            row = dark[y * width:(y + 1) * width]
            scores = [a + b for a, b in zip(scores, row)]
            samples += 1
        x_groups = [
            g for g in _groups([x for x, score in enumerate(scores) if score >= samples * 0.72])
            if len(g) <= max(5, width // 150)
        ]
        # A screenshot contains repeated button/checkbox edges as well as the
        # table rules. A button on every row can exceed 72% total coverage but
        # its edge stops in every row's padding. Real column rules continue
        # across row boundaries, including the actual-time subcolumn divider
        # that starts below the first header tier.
        required_run = min(samples * 0.5, statistics.median(b - a for a, b in zip(ys, ys[1:])) * 2 / step)
        filled_rows = {y for group in all_groups if len(group) > max(4, height // 150) for y in group}
        unfilled_samples = sum(y not in filled_rows for y in range(top, bottom + 1, step))
        required_run = min(required_run, max(1, unfilled_samples * 0.85))
        column_rules = []
        for group in x_groups:
            longest = current = 0
            for y in range(top, bottom + 1, step):
                # A filled header supports every x; it cannot prove that a
                # repeated control edge continues through the table body.
                if y not in filled_rows and any(dark[y * width + x] for x in group):
                    current += 1
                    longest = max(longest, current)
                else:
                    current = 0
            if longest >= required_run:
                column_rules.append(group)
        xs = [sum(g) / len(g) for g in column_rules]
        # At a lighter threshold the tinted header can merge with its rules
        # and disappear as a thick filled band. Once there are enough columns
        # for a supported form, preserve the most row boundaries first. An
        # extra faint, clipped column must not outweigh losing the first trip.
        if (len(xs) >= 13, len(ys), len(xs)) > (len(best[0]) >= 13, len(best[1]), len(best[0])):
            best = xs, ys
    return best


def _ocr_paths() -> tuple[str, dict[str, str]]:
    root = Path(__file__).resolve().parent
    candidates = [root / "ocr" / "tesseract.exe", root.parent / "ocr" / "tesseract.exe"]
    executable = next((str(p) for p in candidates if p.is_file()), None) if os.name == "nt" else None
    executable = executable or shutil.which("tesseract")
    if not executable:
        raise ValueError("OCR 실행 파일을 찾지 못했습니다. 배포 파일의 OCR 구성요소를 확인해 주세요.")
    env = os.environ.copy()
    model_dirs = [root / "ocr" / "tessdata", root.parent / "ocr" / "tessdata"]
    model_dir = next((p for p in model_dirs if (p / "kor.traineddata").is_file() and (p / "eng.traineddata").is_file()), None)
    if model_dir:
        env["TESSDATA_PREFIX"] = str(model_dir)
    env["OMP_THREAD_LIMIT"] = "2"
    return executable, env


def _read_tsv(text: str, scale: float) -> list[Word]:
    words = []
    try:
        rows = csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE)
        for row in rows:
            if row.get("level") != "5" or not (row.get("text") or "").strip():
                continue
            words.append(Word(
                row["text"].strip(), float(row["left"]) / scale,
                float(row["top"]) / scale, float(row["width"]) / scale,
                float(row["height"]) / scale, float(row["conf"]),
            ))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("OCR 결과 형식이 올바르지 않습니다. 다른 캡처 이미지로 다시 시도해 주세요.") from exc
    return words


def _run_ocr(pix: fitz.Pixmap, scale: float, *, numeric: bool = False, psm: int = 6, language: str = "kor+eng") -> list[Word]:
    enlarged = fitz.Pixmap(pix, round(pix.width * scale), round(pix.height * scale))
    executable, env = _ocr_paths()
    working_directory = None
    if os.name == "nt" and env.get("TESSDATA_PREFIX"):
        # Windows Tesseract converts the environment path through the system
        # code page. A Korean AppData path can become "??". CreateProcessW
        # accepts a Unicode working directory, so load the models relative to it.
        working_directory = env["TESSDATA_PREFIX"]
        env = env.copy()
        env["TESSDATA_PREFIX"] = "."
    # Leptonica's Windows file reader cannot reliably open a PNG below a Korean
    # user/TEMP path. Feed the image bytes through stdin, without a temp file.
    command = [executable, "stdin", "stdout", "-l", "eng" if numeric else language, "--psm", str(psm), "-c", "tessedit_create_tsv=1"]
    if numeric:
        command += ["-c", "tessedit_char_whitelist=0123456789:"]
    try:
        result = subprocess.run(
            command, input=enlarged.tobytes("png"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=working_directory,
            timeout=90, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("문자 인식 시간이 초과되었습니다. 표 부분만 잘라서 다시 넣어 주세요.") from exc
    except OSError as exc:
        raise ValueError("OCR 프로그램을 실행하지 못했습니다. 배포 파일의 OCR 구성요소를 확인해 주세요.") from exc
    if result.returncode:
        error = result.stderr.decode("utf-8", errors="replace")
        if "traineddata" in error or "Failed loading language" in error:
            raise ValueError("한국어/영어 OCR 학습파일을 불러오지 못했습니다. OCR 구성요소를 확인해 주세요.")
        raise ValueError("문자 인식에 실패했습니다. 선명한 PNG 또는 JPG 캡처로 다시 시도해 주세요.")
    return _read_tsv(result.stdout.decode("utf-8", errors="replace"), scale)


def _recognize(pix: fitz.Pixmap, xs: list[float], ys: list[float]) -> list[Word]:
    # Erase only detected grid rules before enlarging the original screenshot.
    prepared = fitz.Pixmap(pix)
    white = (255,) * prepared.n
    for x in xs:
        prepared.set_rect(fitz.IRect(max(0, int(x) - 1), 0, min(prepared.width, int(x) + 2), prepared.height), white)
    for y in ys:
        prepared.set_rect(fitz.IRect(0, max(0, int(y) - 1), prepared.width, min(prepared.height, int(y) + 2)), white)
    scale = min(3.0, 5000 / prepared.width, 6500 / prepared.height)
    if scale < 1:
        scale = 1.0
    # Sparse-text mode handles columns with different line counts per row.
    return _run_ocr(prepared, scale, psm=11)


def _refine_actual_times(pix: fitz.Pixmap, words: list[Word], xs: list[float], ys: list[float], first_data_row: int = 1) -> list[Word]:
    """Retry only an unreadable actual-time cell using a numeric OCR alphabet."""
    raw, stride, channels = pix.samples, pix.stride, pix.n
    result = list(words)
    for top, bottom in zip(ys[first_data_row:-1], ys[first_data_row + 1:]):
        cell_words = [w for w in result if xs[5] < w.left + w.width / 2 < xs[6] and top < w.top + w.height / 2 < bottom]
        original = _cell_text(cell_words)
        if not original or len(_times(original)) == 2:
            continue
        x1, x2 = int(xs[5]) + 3, int(xs[6]) - 2
        y1, y2 = int(top) + 3, int(bottom) - 2
        if x2 <= x1 or y2 <= y1:
            continue
        pixels = b"".join(raw[y * stride + x1 * channels:y * stride + x2 * channels] for y in range(y1, y2))
        crop = fitz.Pixmap(pix.colorspace, x2 - x1, y2 - y1, pixels, False)
        numeric_words = _run_ocr(crop, 4)
        numeric_text = _cell_text(numeric_words)
        times = _times(numeric_text)
        if len(times) != 2:
            numeric_words = _run_ocr(crop, 4, numeric=True)
            numeric_text = _cell_text(numeric_words)
            times = _times(numeric_text)
        if len(times) != 2:
            continue
        # Keep uncertainty visible: supplemental recognition never raises confidence.
        confidence = min([w.confidence for w in cell_words] + [w.confidence for w in numeric_words] + [60])
        result = [w for w in result if w not in cell_words]
        result.append(Word("\n".join(times), x1, y1, x2 - x1, y2 - y1, confidence))
    return result


def _column_candidates(pix: fitz.Pixmap, xs: list[float], ys: list[float], col: int, scale: float, first_data_row: int = 1) -> dict[int, list[Word]]:
    """A uniform single text column is more legible than a mixed-height table."""
    x1, x2 = int(xs[col]) + 3, int(xs[col + 1]) - 2
    y1, y2 = int(ys[first_data_row]) + 2, int(ys[-1]) - 2
    if x2 <= x1 or y2 <= y1:
        return {}
    raw, stride, channels = pix.samples, pix.stride, pix.n
    pixels = b"".join(raw[y * stride + x1 * channels:y * stride + x2 * channels] for y in range(y1, y2))
    crop = fitz.Pixmap(pix.colorspace, x2 - x1, y2 - y1, pixels, False)
    for line in ys[first_data_row + 1:-1]:
        y = int(line) - y1
        crop.set_rect(fitz.IRect(0, max(0, y - 1), crop.width, min(crop.height, y + 2)), (255,) * crop.n)
    recognized = _run_ocr(crop, scale, language="kor+eng")
    by_row: dict[int, list[Word]] = defaultdict(list)
    for w in recognized:
        moved = Word(w.text, w.left + x1, w.top + y1, w.width, w.height, w.confidence)
        row = bisect.bisect_right(ys, moved.top + moved.height / 2) - 1
        if first_data_row <= row < len(ys) - 1:
            by_row[row].append(moved)
    return dict(by_row)


def _refine_identity_columns(pix: fitz.Pixmap, words: list[Word], xs: list[float], ys: list[float], first_data_row: int = 1) -> tuple[list[Word], dict[int, list[str]]]:
    """Cross-check financial grouping names rather than trusting one OCR pass.

    OCR confidence is not proof of correctness. Keep disagreements as explicit
    review warnings even when two recognition views agree on a candidate.
    """
    result = list(words)
    notes: dict[int, list[str]] = defaultdict(list)
    original: dict[tuple[int, int], list[Word]] = {}
    for row in range(first_data_row, len(ys) - 1):
        for col in (2, 3, 6, 10, 11):
            original[row, col] = [w for w in words if xs[col] < w.left + w.width / 2 < xs[col + 1] and ys[row] < w.top + w.height / 2 < ys[row + 1]]
    name_small = _column_candidates(pix, xs, ys, 3, 3, first_data_row)
    name_large = _column_candidates(pix, xs, ys, 3, 5, first_data_row)
    selected: dict[int, str] = {}
    alternatives: dict[int, list[str]] = {}
    for row in range(first_data_row, len(ys) - 1):
        candidates = [re.sub(r"\s+", "", _cell_text(ws)) for ws in (original[row, 3], name_small.get(row, []), name_large.get(row, []))]
        candidates = [value for value in candidates if value and re.fullmatch(r"[가-힣A-Za-z·.\-]+", value)]
        if candidates:
            counts = Counter(candidates)
            selected[row] = max(reversed(candidates), key=lambda value: counts[value])
            alternatives[row] = list(dict.fromkeys(candidates))
    collisions: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row, name in selected.items():
        dates = _dates(_cell_text(original[row, 6]))
        if dates:
            collisions[dates[0], name].append(row)
    for group in collisions.values():
        if len(group) < 2:
            continue
        high_resolution = {row: re.sub(r"\s+", "", _cell_text(name_large.get(row, []))) for row in group}
        if len(set(high_resolution.values())) > 1:
            for row in group:
                candidate = high_resolution[row]
                if candidate and candidate != selected[row] and re.fullmatch(r"[가-힣A-Za-z·.\-]+", candidate):
                    selected[row] = candidate
                    notes[row].append("성명 인식 차이로 같은 사람의 출장으로 잘못 합산될 수 있습니다. 원본 성명을 반드시 대조해 주세요.")
    for row, name in selected.items():
        candidates = alternatives[row]
        if len(candidates) > 1:
            notes[row].append("성명 인식 후보가 다릅니다(" + " / ".join(candidates) + "). 원본과 대조해 주세요.")
        old = original[row, 3]
        result = [w for w in result if w not in old]
        confidence = 60 if len(candidates) > 1 else min([w.confidence for w in name_small.get(row, []) + name_large.get(row, [])] or [60])
        result.append(Word(name, xs[3] + 3, ys[row] + 3, xs[4] - xs[3] - 6, ys[row + 1] - ys[row] - 6, confidence))
    for col, label in ((2, "직급"), (10, "공용차량"), (11, "결재상태")):
        focused = _column_candidates(pix, xs, ys, col, 5, first_data_row)
        for row, candidate_words in focused.items():
            candidate = re.sub(r"\s+", "", _cell_text(candidate_words))
            previous = re.sub(r"\s+", "", _cell_text(original[row, col]))
            if not candidate:
                continue
            # Do not replace legible status/vehicle values with an invalid candidate.
            if col == 10 and candidate not in {"사용", "미사용", "이용", "미이용", "사용안함", "Y", "N", "y", "n", "O", "X"}:
                continue
            if col == 11 and candidate not in {"결재완료", "미신청", "취소", "반려", "결재중", "미결재", "신청", "승인", "처리완료"}:
                continue
            if candidate != previous:
                notes[row].append(label + " 인식 결과가 재인식 전후 다릅니다. 원본과 대조해 주세요.")
            old = original[row, col]
            result = [w for w in result if w not in old]
            confidence = min([w.confidence for w in candidate_words] + ([60] if candidate != previous else []))
            result.append(Word(candidate, xs[col] + 3, ys[row] + 3, xs[col + 1] - xs[col] - 6, ys[row + 1] - ys[row] - 6, confidence))
    return result, dict(notes)


def _cell_text(words: list[Word]) -> str:
    if not words:
        return ""
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w.top + w.height / 2, w.left)):
        if not lines or abs((word.top + word.height / 2) - (lines[-1][0].top + lines[-1][0].height / 2)) > max(4, word.height * 0.65):
            lines.append([word])
        else:
            lines[-1].append(word)
    rendered = []
    for line in lines:
        ordered = sorted(line, key=lambda w: w.left)
        text = ordered[0].text
        for previous, word in zip(ordered, ordered[1:]):
            gap = word.left - previous.left - previous.width
            adjacent_korean = bool(re.search(r"[가-힣]$", previous.text) and re.match(r"[가-힣]", word.text))
            join = adjacent_korean and gap < max(1.0, min(previous.height, word.height) * 0.25)
            text += ("" if join else " ") + word.text
        rendered.append(text)
    return "\n".join(rendered)


_HEADER_LABELS = {
    "department": ("부서", "소속", "부서명"),
    "rank": ("직급", "직위"),
    "name": ("출장자", "성명", "이름"),
    "requested": ("신청출장기간", "출장기간", "신청기간"),
    "destination": ("출장지", "목적지"),
    "purpose": ("출장목적", "목적"),
    "vehicle": ("공용차량", "공용차량사용여부", "차량"),
    "approval": ("결재상태", "결재"),
}
_VEHICLE_LABELS = {"사용", "미사용", "이용", "미이용", "사용안함", "y", "n", "o", "x"}
_APPROVAL_LABELS = {"결재완료", "미신청", "취소", "반려", "결재중", "미결재", "신청", "승인", "처리완료"}


def _compact(text: str) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]", "", unicodedata.normalize("NFKC", text)).lower()


def _one_edit(left: str, right: str) -> bool:
    """Allow one damaged glyph in a longer label, never fuzzy-match 2-letter labels."""
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) > len(right):
        left, right = right, left
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) <= 1
    for index in range(len(right)):
        if right[:index] + right[index + 1:] == left:
            return True
    return False


def _header_score(text: str, role: str) -> float:
    text = _compact(text)
    if role == "purpose" and "목적지" in text and "출장목적" not in text:
        return 0.0
    for label in _HEADER_LABELS[role]:
        if label in text:
            return 1.0
    for label in _HEADER_LABELS[role]:
        if len(label) < 3:
            continue
        for length in (len(label) - 1, len(label), len(label) + 1):
            for start in range(max(0, len(text) - length + 1)):
                if _one_edit(text[start:start + length], label):
                    return 0.75
    return 0.0


def _physical_cells(words: list[Word], xs: list[float], ys: list[float]) -> list[list[str]]:
    cells: list[list[list[Word]]] = [[[] for _ in range(len(xs) - 1)] for _ in range(len(ys) - 1)]
    for word in words:
        col = bisect.bisect_right(xs, word.left + word.width / 2) - 1
        row = bisect.bisect_right(ys, word.top + word.height / 2) - 1
        if 0 <= col < len(xs) - 1 and 0 <= row < len(ys) - 1:
            cells[row][col].append(word)
    return [[_cell_text(cell) for cell in row] for row in cells]


def _body_start(rows: list[list[str]]) -> int | None:
    for index, cells in enumerate(rows):
        if any(_dates(cell) for cell in cells):
            return index
        numbered = any(re.fullmatch(r"\s*\d{1,3}[.)]?\s*", cell) for cell in cells[:3])
        if numbered and sum(len(_times(cell)) for cell in cells) >= 1:
            return index
        identifier = any(re.fullmatch(r"[A-Za-z0-9\-]{4,30}", re.sub(r"\s+", "", cell))
                         and len(re.findall(r"\d", cell)) >= 4 for cell in cells)
        state = any(_compact(cell) in _VEHICLE_LABELS | _APPROVAL_LABELS for cell in cells)
        if identifier and state:
            return index
    return None


def _recognize_header(pix: fitz.Pixmap, words: list[Word], xs: list[float], ys: list[float], end_row: int | None = None) -> list[Word]:
    """Retry a small header band rather than making the complete table larger."""
    rows = _physical_cells(words, xs, ys)
    first = end_row if end_row is not None else _body_start(rows)
    if first == 0:
        return []  # The screenshot begins with a data row; no header is present.
    end = first if first is not None else min(2, len(ys) - 1)
    x1, x2 = max(0, int(xs[0])), min(pix.width, int(xs[-1]) + 1)
    y1, y2 = max(0, int(ys[0])), min(pix.height, int(ys[end]))
    if x2 <= x1 or y2 <= y1:
        return []
    raw, stride, channels = pix.samples, pix.stride, pix.n
    samples = b"".join(raw[y * stride + x1 * channels:y * stride + x2 * channels] for y in range(y1, y2))
    crop = fitz.Pixmap(pix.colorspace, x2 - x1, y2 - y1, samples, False)
    white = (255,) * crop.n
    for x in xs:
        local = int(x) - x1
        crop.set_rect(fitz.IRect(max(0, local - 1), 0, min(crop.width, local + 2), crop.height), white)
    for y in ys[:end + 1]:
        local = int(y) - y1
        crop.set_rect(fitz.IRect(0, max(0, local - 1), crop.width, min(crop.height, local + 2)), white)
    scale = max(1.0, min(5.0, 6500 / crop.width, 1800 / crop.height))
    retried = _run_ocr(crop, scale, psm=6)
    if not retried:
        return []
    return [Word(w.text, w.left + x1, w.top + y1, w.width, w.height, w.confidence) for w in retried]


def _resolve_layout(words: list[Word], xs: list[float], ys: list[float]) -> TableLayout:
    """Use labels when readable, otherwise require a unique body-validated layout.

    A date column alone is not evidence of a travel form. Body recovery also
    checks the neighboring actual-time column, row numbers, names, employee-ID
    shape and vehicle/approval fields. Requested times never supply actual times.
    """
    if len(xs) < 13 or len(ys) < 3:
        raise ValueError("출장 목록 표를 찾지 못했습니다. 열 제목과 표 테두리가 모두 보이도록 캡처해 주세요.")
    rows = _physical_cells(words, xs, ys)
    first_body = _body_start(rows)
    header_end = first_body if first_body is not None else min(2, len(rows))
    headers = [" ".join(row[col] for row in rows[:header_end]) for col in range(len(xs) - 1)]
    candidates = []
    for department in range(1, min(5, len(xs) - 10)):
        for actual_span in (1, 2):
            requested = department + 4 + actual_span
            if requested + 6 >= len(xs):
                continue
            roles = {"department": department, "rank": department + 1, "name": department + 2,
                     "requested": requested, "destination": requested + 1, "purpose": requested + 2,
                     "vehicle": requested + 4, "approval": requested + 5}
            scores = {role: _header_score(headers[column], role) for role, column in roles.items()}
            header_score = sum(scores.values())
            # A clearly identified column in a different position is not an OCR
            # spelling error. Never shift its values into the proposed column.
            contradiction = False
            for role, expected in roles.items():
                exact = [index for index, text in enumerate(headers) if _header_score(text, role) == 1]
                if exact and expected not in exact:
                    contradiction = True
                    break
            if contradiction:
                continue
            evidence = []
            body_rows = []
            numbers = []
            date_rows = 0
            clear_header_rows = []
            partial_rows = set()
            for index, cells in enumerate(rows):
                number = cells[department - 1].strip()
                name = re.sub(r"\s+", "", cells[department + 2])
                identifier = re.sub(r"\s+", "", cells[department + 3])
                actual = " ".join(cells[department + 4:requested])
                dates = _dates(cells[requested])
                times = _times(actual)
                numbered = re.fullmatch(r"\d{1,3}[.)]?", number) is not None
                name_valid = re.fullmatch(r"[가-힣A-Za-z·.\-]{2,30}", name) is not None
                id_valid = (re.fullmatch(r"[A-Za-z0-9\-]{4,30}", identifier) is not None
                            and len(re.findall(r"\d", identifier)) >= 4)
                metadata = sum(bool(re.search(r"[가-힣A-Za-z]{2}", _compact(cells[c]))) for c in (department, department + 1))
                vehicle = _compact(cells[requested + 4]) in _VEHICLE_LABELS
                approval = _compact(cells[requested + 5]) in _APPROVAL_LABELS
                row_headers = sum(_header_score(cells[column], role) > 0 for role, column in roles.items())
                if row_headers >= 2 and not (numbered or dates or times or id_valid or vehicle or approval):
                    clear_header_rows.append(index)
                if name_valid and metadata and (id_valid or vehicle or approval or row_headers < 2):
                    partial_rows.add(index)
                record_metadata = name_valid or metadata or id_valid or vehicle or approval
                if not (numbered or ((dates or times) and record_metadata)):
                    continue
                body_rows.append(index)
                if numbered:
                    numbers.append(int(re.sub(r"\D", "", number)))
                date_rows += bool(dates)
                identity_supported = id_valid or metadata == 2
                number_supported = numbered or (not re.search(r"[가-힣]", number)
                                                 and len(_compact(number)) <= 3
                                                 and vehicle and approval and metadata == 2)
                strong = len(dates) >= 2 and len(times) == 2 and number_supported and name_valid and identity_supported and metadata >= 1 and (vehicle or approval)
                evidence.append((strong, vehicle and approval and metadata == 2))
            strong_count = sum(item[0] for item in evidence)
            sequence = len(numbers) == len(set(numbers)) and all(b > a for a, b in zip(numbers, numbers[1:]))
            # Full readable headings also support rows whose actual time is
            # missing. Missing values remain blank and therefore need review.
            trusted_header = header_score >= 5 and any(scores[k] for k in ("department", "name", "requested"))
            minimum = 1 if len(body_rows) == 1 and evidence and evidence[0][1] else 2
            body_supported = (strong_count >= minimum and strong_count >= len(body_rows) * 0.6 and sequence)
            if not (trusted_header or body_supported):
                continue
            # If a table body has dates but none in this candidate's period
            # column, even otherwise plausible labels cannot validate its layout.
            if first_body is not None and body_rows and not date_rows:
                continue
            start = min(body_rows) if body_rows else (header_end if header_end < len(rows) else 1)
            # An unreadable No/date/time in the first trip is still a trip.
            # Use recognizable header rows and adjacent identity/metadata rows
            # to retain it with missing-value warnings rather than dropping it.
            preceding_headers = [index for index in clear_header_rows if index < start]
            if preceding_headers:
                start = min(start, max(preceding_headers) + 1)
            else:
                while start > 0 and start - 1 in partial_rows:
                    start -= 1
            indices = [department - 1, department, department + 1, department + 2, department + 3, department + 4]
            indices += list(range(requested, requested + 7))
            recovered = not trusted_header or not all(scores[k] == 1 for k in ("department", "name", "requested"))
            score = header_score * 2 + (strong_count / max(1, len(body_rows))) * 8
            candidates.append((score, TableLayout([xs[index] for index in indices], start, recovered)))
    if not candidates:
        raise ValueError("표의 열 배치를 확정하지 못했습니다. 부서·실제 확인시각·신청 출장기간·결재상태가 보이는 원본 캡처로 다시 시도해 주세요.")
    candidates.sort(key=lambda item: item[0], reverse=True)
    if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 2:
        raise ValueError("표의 열 배치가 두 가지 이상으로 읽힙니다. 부서부터 결재상태까지 보이도록 표 부분을 잘라 다시 넣어 주세요.")
    return candidates[0][1]


def _logical_grid(words: list[Word], xs: list[float], ys: list[float]) -> list[float]:
    """Backward-compatible helper; the parser also keeps the validated data start."""
    return _resolve_layout(words, xs, ys).xs


def _dates(text: str) -> list[str]:
    found = []
    for match in _DATE.finditer(text.replace("—", "-").replace("–", "-")):
        try:
            found.append(date(*map(int, match.groups())).isoformat())
        except ValueError:
            pass
    return found


def _times(text: str) -> list[str]:
    return [f"{int(m[1]):02d}:{m[2]}" for m in _TIME.finditer(text) if int(m[1]) < 24]


def _parse_cells(cells: dict[str, str], row_number: int) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    name = re.sub(r"\s+", "", cells.get("name", ""))
    if not name or not re.search(r"[가-힣A-Za-z]", name):
        name = ""
        warnings.append(f"{row_number}행: 출장자 이름을 읽지 못했습니다. 원본과 대조해 직접 입력해 주세요.")
    actual = cells.get("actual", "")
    actual_times = _times(actual)
    start_time = end_time = ""
    if len(actual_times) == 2:
        start_time, end_time = actual_times
    elif len(actual_times) == 1:
        compact_actual = re.sub(r"\s+", "", actual)
        if re.search(r"시작\D*" + re.escape(actual_times[0]), compact_actual):
            start_time = actual_times[0]
        elif re.search(r"종료\D*" + re.escape(actual_times[0]), compact_actual):
            end_time = actual_times[0]
        warnings.append(f"{row_number}행: 실제 시작·종료 확인시각이 모두 인식되지 않았습니다. 신청시간으로 대체하지 않았습니다.")
    elif len(actual_times) > 2:
        warnings.append(f"{row_number}행: 확인시각이 여러 개여서 자동 선택하지 않았습니다. 실제 시각을 확인해 주세요.")
    else:
        warnings.append(f"{row_number}행: 실제 확인시각이 없습니다. 신청시간으로 대체하지 않았습니다.")
    requested_dates = _dates(cells.get("requested", ""))
    requested_times = _times(cells.get("requested", ""))
    actual_dates = _dates(actual)
    dates = actual_dates or requested_dates
    start_date = dates[0] if dates else ""
    end_date = dates[-1] if len(dates) >= 2 else ""
    if not dates:
        warnings.append(f"{row_number}행: 출장 날짜를 읽지 못했습니다.")
    elif len(dates) == 1:
        warnings.append(f"{row_number}행: 종료 날짜를 읽지 못했습니다. 시작 날짜로 임의 대체하지 않았습니다.")
    if start_date and end_date and start_date > end_date:
        warnings.append(f"{row_number}행: 종료일이 시작일보다 빠릅니다. 날짜를 확인해 주세요.")
    vehicle_text = re.sub(r"\s+", "", cells.get("vehicle", "")).lower()
    if vehicle_text in {"n", "x", "미사용", "미이용", "사용안함", "아니오", "없음", "비사용"} or "미사용" in vehicle_text or "미이용" in vehicle_text:
        vehicle = "unused"
    elif vehicle_text in {"y", "o", "사용", "이용", "사용함", "예", "있음"}:
        vehicle = "used"
    else:
        vehicle = "unknown"
        warnings.append(f"{row_number}행: 공용차량 사용 여부를 확인해 주세요.")
    trip = {
        "department": re.sub(r"\s+", " ", cells.get("department", "")).strip(),
        "rank": re.sub(r"\s+", " ", cells.get("rank", "")).strip(),
        "name": name,
        "start_date": start_date, "end_date": end_date,
        "start_time": start_time, "end_time": end_time,
        "scope": "관내", "vehicle": vehicle,
        "destination": re.sub(r"\s+", " ", cells.get("destination", "")).strip(),
        "purpose": re.sub(r"\s+", " ", cells.get("purpose", "")).strip(),
        "daily_allowance": None, "meal": 0, "lodging": 0,
        "transport": 0, "other": 0, "claim_amount": None,
        "approval_status": re.sub(r"\s+", "", cells.get("approval", "")),
        "requested_start_date": requested_dates[0] if requested_dates else "",
        "requested_end_date": requested_dates[-1] if len(requested_dates) >= 2 else "",
        "requested_start_time": requested_times[0] if requested_times else "",
        "requested_end_time": requested_times[1] if len(requested_times) == 2 else "",
    }
    return trip, warnings


def _parse_table(words: list[Word], xs: list[float], ys: list[float], review_notes: dict[int, list[str]] | None = None, first_data_row: int | None = None) -> tuple[list[dict], list[str]]:
    if len(xs) != 13 or len(ys) < 3:
        raise ValueError("12열 출장 목록 표를 찾지 못했습니다. 열 제목과 표 테두리가 모두 보이도록 캡처해 주세요.")
    rows: list[list[list[Word]]] = [[[] for _ in _COLUMNS] for _ in range(len(ys) - 1)]
    for word in words:
        col = bisect.bisect_right(xs, word.left + word.width / 2) - 1
        row = bisect.bisect_right(ys, word.top + word.height / 2) - 1
        if 0 <= col < 12 and 0 <= row < len(rows):
            rows[row][col].append(word)
    texts = [{key: _cell_text(cell) for key, cell in zip(_COLUMNS, row)} for row in rows]
    if first_data_row is None:
        header = -1
        for index, cells in enumerate(texts[:4]):
            compact = re.sub(r"\s+", "", " ".join(cells.values()))
            if sum(label in compact for label in ("부서", "직급", "출장자", "출장지", "출장목적", "결재")) >= 3:
                header = index
                break
        if header == -1:
            raise ValueError("표의 열 배치를 확정하지 못했습니다. 원본 캡처의 열 제목과 표 부분을 확인해 주세요.")
        first_data_row = header + 1
    trips, warnings = [], []
    for index in range(first_data_row, len(texts)):
        cells = texts[index]
        if not any(cells.values()):
            continue
        # A second header tier may have its own full-width rule.
        if not re.search(r"\d", cells.get("number", "")) and not cells.get("name") and not _dates(cells.get("requested", "")) and not _times(cells.get("actual", "")):
            continue
        number = len(trips) + 1
        trip, notes = _parse_cells(cells, number)
        notes.extend(f"{number}행: {note}" for note in (review_notes or {}).get(index, []))
        confidences = [w.confidence for col, ws in enumerate(rows[index]) if col != 4 for w in ws if w.confidence >= 0]
        if confidences and (min(confidences) < 35 or sum(confidences) / len(confidences) < 65):
            notes.append(f"{number}행: 인식 신뢰도가 낮은 문자가 있습니다. 원본과 대조해 주세요.")
        trip["ocr_warnings"] = list(notes)
        trips.append(trip)
        warnings.extend(notes)
    if not trips:
        raise ValueError("인식된 출장 내역이 없습니다. 빈 목록인지, 글자가 선명한지 확인해 주세요.")
    return trips, warnings


def parse_image(image_bytes: bytes, filename: str = "capture.png") -> dict:
    """Extract reviewable travel entries from an image using bundled local OCR."""
    if not image_bytes:
        raise ValueError("이미지 파일이 비어 있습니다.")
    if len(image_bytes) > 30 * 1024 * 1024:
        raise ValueError("이미지는 30MB 이하로 넣어 주세요.")
    try:
        pix = fitz.Pixmap(image_bytes)
        if pix.alpha:
            pix = fitz.Pixmap(pix, 0)
        if pix.colorspace is None or pix.colorspace.n != 3:
            pix = fitz.Pixmap(fitz.csRGB, pix)
    except Exception as exc:
        raise ValueError("이미지를 열 수 없습니다. PNG 또는 JPG 캡처 파일을 넣어 주세요.") from exc
    if pix.width < 400 or pix.height < 80:
        raise ValueError("이미지가 너무 작습니다. 출장 목록 전체를 더 크게 캡처해 주세요.")
    if pix.width > 12000 or pix.height > 12000 or pix.width * pix.height > 18000000:
        raise ValueError("이미지가 너무 큽니다. 출장 목록 표 부분만 잘라서 넣어 주세요.")
    xs, ys = _grid(pix)
    if len(xs) < 13 or len(ys) < 3:
        raise ValueError("출장 목록 표를 찾지 못했습니다. 열 제목과 표 테두리가 모두 보이도록 캡처해 주세요.")
    words = _recognize(pix, xs, ys)
    if not words:
        raise ValueError("이미지에서 글자를 읽지 못했습니다. 선명한 캡처 파일을 넣어 주세요.")
    # The source system also exports a distinct 18-column monthly report.
    # Its geometry and field meanings must be handled before the 12-column UI.
    if len(xs) >= 18:
        if __package__:
            from .monthly_report import try_parse_monthly_image
        else:
            from monthly_report import try_parse_monthly_image
        monthly = try_parse_monthly_image(pix, xs, ys, words, filename)
        if monthly is not None:
            return monthly
    layout = None
    try:
        layout = _resolve_layout(words, xs, ys)
    except ValueError:
        pass
    retried_header = layout is None or layout.recovered
    if retried_header:
        header_end = layout.first_data_row if layout is not None else _body_start(_physical_cells(words, xs, ys))
        if header_end is None:
            header_end = min(2, len(ys) - 1)
        header_words = _recognize_header(pix, words, xs, ys, header_end)
        # A second OCR view replaces the header band; never append a second
        # copy of a date/time/name that could be mistaken for another value.
        def in_header(word):
            return (xs[0] <= word.left + word.width / 2 < xs[-1]
                    and ys[0] <= word.top + word.height / 2 < ys[header_end])
        header_words = [word for word in header_words if in_header(word)]
        combined = [word for word in words if not in_header(word)] + header_words if header_words else words
        try:
            retried_layout = _resolve_layout(combined, xs, ys)
        except ValueError:
            if layout is None:
                raise
            # A second OCR view can be worse than the first. A previously
            # unique body-validated layout remains a reviewable recovery.
        else:
            if layout is not None and layout.xs != retried_layout.xs:
                raise ValueError("표의 열 배치가 재인식 전후 다릅니다. 원본 표 부분을 잘라 다시 넣어 주세요.")
            words = combined
            layout = retried_layout
    logical_xs = layout.xs
    words, review_notes = _refine_identity_columns(pix, words, logical_xs, ys, layout.first_data_row)
    words = _refine_actual_times(pix, words, logical_xs, ys, layout.first_data_row)
    if layout.recovered:
        note = "열 제목 일부를 읽지 못해 표선·본문 날짜·실제시각·이름/차량 정보를 교차 확인하여 열 배치를 복구했습니다. 원본의 각 열과 인식 결과가 맞는지 반드시 대조해 주세요."
        for index in range(layout.first_data_row, len(ys) - 1):
            review_notes.setdefault(index, []).append(note)
    trips, warnings = _parse_table(words, logical_xs, ys, review_notes, layout.first_data_row)
    warnings.insert(0, "OCR 결과입니다. 이름·날짜·실제 확인시각·공용차량 사용 여부를 원본과 대조한 뒤 PDF를 만드세요.")
    source_name = re.split(r"[/\\]", str(filename))[-1] or "capture.png"
    return {"trips": trips, "warnings": warnings, "source_name": source_name}
