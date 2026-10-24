"""Offline, loopback-only application and command line entrypoint."""
from __future__ import annotations

import argparse
import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import traceback
import webbrowser

from renderer import recalculate, render_pdf

MAX_PDF = 20 * 1024 * 1024
MAX_REQUEST = 29 * 1024 * 1024
APP_VERSION = "0.3.0"


def failure_details(exc, route):
    """Describe the failing code, never exception text, local paths or input data."""
    import fitz

    code, message = {
        "/api/render": ("PDF_RENDER_FAILED", "PDF를 만드는 중 오류가 발생했습니다. 입력 내역은 유지됩니다. 아래 오류 정보를 복사해 알려 주세요."),
        "/api/parse": ("INPUT_PARSE_FAILED", "자료를 읽는 중 오류가 발생했습니다. 아래 오류 정보를 복사해 알려 주세요."),
        "/api/recalculate": ("CALCULATION_FAILED", "금액을 계산하는 중 오류가 발생했습니다. 아래 오류 정보를 복사해 알려 주세요."),
    }.get(route, ("REQUEST_FAILED", "요청을 처리하지 못했습니다. 아래 오류 정보를 복사해 알려 주세요."))
    frames = []
    for frame, lineno in traceback.walk_tb(exc.__traceback__):
        filename = Path(frame.f_code.co_filename).name
        frames.append(f"{filename}:{lineno} {frame.f_code.co_name}")
    details = "\n".join([
        f"TravelClaim v{APP_VERSION} / {code} / {secrets.token_hex(4).upper()}",
        f"Python {sys.version.split()[0]} / {sys.platform} / PyMuPDF {fitz.VersionBind}",
        f"Exception: {type(exc).__name__}",
        *frames[-10:],
    ])
    # The launcher's local startup log receives only the same safe diagnostic.
    # In particular, don't print traceback source lines, locals or exc.__str__().
    try:
        print(json.dumps({"diagnostics": details}, ensure_ascii=True), file=sys.stderr, flush=True)
    except OSError:
        pass
    return {"error": message, "error_code": code, "diagnostics": details}


def validation_details(exc, route):
    """Return useful 400 diagnostics without copying recognized text or paths."""
    stage = {"/api/parse": "parse", "/api/render": "render",
             "/api/recalculate": "calculate"}.get(route, "request")
    code = getattr(exc, "error_code", "INPUT_VALIDATION_FAILED")
    if not isinstance(code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code):
        code = "INPUT_VALIDATION_FAILED"

    def counts_only(value, depth=0):
        if not isinstance(value, dict) or depth > 2:
            return {}
        clean = {}
        for key, count in list(value.items())[:30]:
            if not isinstance(key, str) or not re.fullmatch(r"[a-z_]{1,48}", key):
                continue
            if isinstance(count, int) and -1000000000 <= count <= 1000000000:
                clean[key] = count
            elif isinstance(count, float) and math.isfinite(count) and abs(count) <= 1000000000:
                clean[key] = count
            elif isinstance(count, dict):
                clean[key] = counts_only(count, depth + 1)
        return clean

    detail = f"TravelClaim v{APP_VERSION}\nstage={stage}\ncode={code}"
    counts = counts_only(getattr(exc, "diagnostics", None))
    if counts:
        detail += "\n" + json.dumps(counts, ensure_ascii=True, sort_keys=True)
    return {"error": str(exc), "error_code": code, "diagnostics": detail}


def decode_input(data):
    try:
        raw = base64.b64decode(data.get("data", ""), validate=True)
    except (ValueError, TypeError):
        raise ValueError("파일 데이터가 손상되었습니다. 파일을 다시 선택해 주세요.") from None
    if not raw or len(raw) > MAX_PDF:
        raise ValueError("20MB 이하의 출장 내역 PDF 파일을 선택해 주세요.")
    return raw, str(data.get("filename", "report.pdf"))[:240]


def parse_pdf_input(payload, filename):
    if not payload or len(payload) > MAX_PDF:
        raise ValueError("20MB 이하의 출장 내역 PDF 파일을 선택해 주세요.")
    if not payload.lstrip().startswith(b"%PDF-"):
        raise ValueError("출장 내역 PDF 파일을 선택해 주세요. 이미지 파일은 지원하지 않습니다.")
    from monthly_report import parse_pdf
    parsed = parse_pdf(payload, filename)
    for trip in parsed["trips"]:
        if trip.get("approval_status", "") != "결재완료":
            trip["include"] = False
    calculated = recalculate(parsed["trips"])
    parsed["trips"] = calculated["trips"]
    parsed["rates"] = calculated["rates"]
    parsed["warnings"] = list(dict.fromkeys(parsed.get("warnings", []) + calculated["warnings"]))
    return parsed


class AppServer(HTTPServer):
    def __init__(self, port=0):
        self.session_token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.host = f"127.0.0.1:{self.server_port}"
        self.url = "http://" + self.host


class Handler(BaseHTTPRequestHandler):
    server_version = "TravelClaim/" + APP_VERSION

    def log_message(self, *args):
        pass  # Names, image contents, and application records are never logged.

    def respond(self, code, body, content_type="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'")
        self.end_headers()
        self.wfile.write(body)

    def correct_host(self):
        if self.headers.get("Host") != self.server.host:
            self.respond(403, {"error": "이 프로그램의 로컬 창에서 실행해 주세요."})
            return False
        return True

    def do_GET(self):
        if not self.correct_host():
            return
        if self.path == "/":
            html = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")
            self.respond(200, html.replace("__SESSION_TOKEN__", self.server.session_token), "text/html; charset=utf-8")
        elif self.path == "/health":
            self.respond(200, {"status": "ok", "offline": True})
        else:
            self.respond(404, {"error": "찾을 수 없는 경로입니다."})

    def do_POST(self):
        if not self.correct_host():
            return
        origin = self.headers.get("Origin")
        token = self.headers.get("X-Session-Token", "")
        if origin not in (None, self.server.url) or not secrets.compare_digest(token, self.server.session_token):
            self.respond(403, {"error": "프로그램 창을 새로고침한 뒤 다시 시도해 주세요."})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_REQUEST:
                raise ValueError("요청이 너무 크거나 비어 있습니다. 20MB 이하 PDF를 사용해 주세요.")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("요청 형식이 올바르지 않습니다.")
            if self.path == "/api/parse":
                raw, filename = decode_input(data)
                result = parse_pdf_input(raw, filename)
            elif self.path == "/api/recalculate":
                result = recalculate(data.get("trips"), data.get("rates"))
            elif self.path == "/api/render":
                allowed = ("trips", "rates", "officer_title", "officer_name", "source_name")
                kwargs = {k: data[k] for k in allowed if k in data}
                raw_trips = kwargs.pop("trips", None)
                pdf, result = render_pdf(raw_trips, **kwargs)
                result.update(data=base64.b64encode(pdf).decode("ascii"),
                              filename="여비지급명세서.pdf")
            elif self.path == "/api/shutdown":
                self.respond(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            else:
                self.respond(404, {"error": "찾을 수 없는 경로입니다."})
                return
            self.respond(200, result)
        except (ValueError, TypeError, KeyError) as exc:
            self.respond(400, validation_details(exc, self.path))
        except Exception as exc:
            self.respond(500, failure_details(exc, self.path))


def main():
    parser = argparse.ArgumentParser(description="출장 내역 PDF → 여비지급명세서 PDF (오프라인)")
    parser.add_argument("--desktop", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--convert", metavar="PDF")
    parser.add_argument("--output", metavar="PDF")
    args = parser.parse_args()
    if args.convert:
        if not args.output:
            parser.error("--output PDF가 필요합니다.")
        source = Path(args.convert)
        parsed = parse_pdf_input(source.read_bytes(), source.name)
        pdf, info = render_pdf(parsed["trips"])
        destination = Path(args.output)
        if source.resolve() == destination.resolve():
            parser.error("원본 PDF와 다른 출력 경로를 선택해 주세요.")
        destination.write_bytes(pdf)
        print(json.dumps({"output": str(destination), **info}, ensure_ascii=False))
        return
    with AppServer(args.port) as server:
        if not args.no_browser and os.environ.get("TRAVELCLAIM_NO_BROWSER") != "1":
            webbrowser.open(server.url)
        print(f"local_port={server.server_port}", flush=True)
        print("여비청구서 변환기가 실행 중입니다. 종료하려면 프로그램의 종료 버튼을 누르세요.", flush=True)
        server.serve_forever(poll_interval=0.25)


if __name__ == "__main__":
    main()
