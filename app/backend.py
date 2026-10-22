"""Offline, loopback-only application and command line entrypoint."""
from __future__ import annotations

import argparse
import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import webbrowser

from renderer import recalculate, render_pdf

MAX_IMAGE = 20 * 1024 * 1024
MAX_REQUEST = 29 * 1024 * 1024


def parse_capture(payload, filename):
    if not payload or len(payload) > MAX_IMAGE:
        raise ValueError("20MB 이하의 캡처 이미지 또는 PDF 파일을 선택해 주세요.")
    if payload.lstrip().startswith(b"%PDF-"):
        from monthly_report import parse_pdf
        parsed = parse_pdf(payload, filename)
    else:
        from ocr_parser import parse_image
        parsed = parse_image(payload, filename)
    for trip in parsed["trips"]:
        if any(s in trip.get("approval_status", "") for s in ("미신청", "취소", "반려", "결재중", "미결재")):
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
    server_version = "TravelClaim/0.2.0"

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
                raise ValueError("요청이 너무 크거나 비어 있습니다. 20MB 이하 이미지 또는 PDF를 사용해 주세요.")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("요청 형식이 올바르지 않습니다.")
            if self.path == "/api/parse":
                try:
                    raw = base64.b64decode(data.get("data", ""), validate=True)
                except (ValueError, TypeError):
                    raise ValueError("파일 데이터가 손상되었습니다. 파일을 다시 선택해 주세요.") from None
                result = parse_capture(raw, str(data.get("filename", "capture.png"))[:240])
            elif self.path == "/api/recalculate":
                result = recalculate(data.get("trips"), data.get("rates"))
            elif self.path == "/api/render":
                allowed = ("trips", "rates", "rates_confirmed", "data_confirmed", "officer_title", "officer_name", "source_name")
                kwargs = {k: data[k] for k in allowed if k in data}
                raw_trips = kwargs.pop("trips", None)
                # Only literal booleans confirm financial/review statements.
                kwargs["rates_confirmed"] = kwargs.get("rates_confirmed") is True
                kwargs["data_confirmed"] = kwargs.get("data_confirmed") is True
                pdf, result = render_pdf(raw_trips, **kwargs)
                result.update(data=base64.b64encode(pdf).decode("ascii"),
                              filename="여비지급명세서"+("_검토용" if result["is_draft"] else "")+".pdf")
            elif self.path == "/api/shutdown":
                self.respond(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            else:
                self.respond(404, {"error": "찾을 수 없는 경로입니다."})
                return
            self.respond(200, result)
        except (ValueError, TypeError, KeyError) as exc:
            self.respond(400, {"error": str(exc)})
        except Exception:
            self.respond(500, {"error": "처리 중 오류가 발생했습니다. 다른 캡처로 다시 시도해 주세요."})


def main():
    parser = argparse.ArgumentParser(description="캡처 이미지 또는 출장 월별집계 PDF → 여비지급명세서 PDF (오프라인)")
    parser.add_argument("--desktop", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--convert", metavar="IMAGE_OR_PDF")
    parser.add_argument("--output", metavar="PDF")
    args = parser.parse_args()
    if args.convert:
        if not args.output:
            parser.error("--output PDF가 필요합니다.")
        source = Path(args.convert)
        parsed = parse_capture(source.read_bytes(), source.name)
        pdf, info = render_pdf(parsed["trips"])
        destination = Path(args.output)
        if source.resolve() == destination.resolve():
            parser.error("원본 이미지와 다른 출력 경로를 선택해 주세요.")
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
