#!/usr/bin/env python3
"""Bundle a tested Windows build with editable documents and corresponding source.

Run scripts/build_windows.py first, then record the actual validation outcomes in
.build/release-validation.json. Private attachments and build caches never ship.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
DOWNLOADS = ROOT / "downloads"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_files():
    for folder in ("app", "launcher", "scripts", "tests", "docs"):
        for path in sorted((ROOT / folder).rglob("*")):
            if (path.is_file() and "__pycache__" not in path.parts
                    and path.suffix not in (".pyc", ".pyo") and path.name != "payload.zip"):
                yield path
    for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "requirements.txt",
                 "build-requirements.txt", ".gitignore"):
        yield ROOT / name


def licenses():
    payload = ROOT / ".build/windows_payload"
    yield "licenses/Go/LICENSE", ROOT / ".build/go-1.25.3/go/LICENSE"
    yield "licenses/Python/LICENSE.txt", payload / "python/LICENSE.txt"
    yield "licenses/PyMuPDF/COPYING", payload / "python/Lib/site-packages/pymupdf-1.26.6.dist-info/COPYING"
    yield "licenses/NotoSansCJK/LICENSE.txt", ROOT / "app/fonts/LICENSE.txt"
    for label, folder in (("OCR", ROOT / ".build/ocr_windows/runtime/licenses"),
                          ("fontTools", payload / "python/Lib/site-packages/fonttools-4.61.1.dist-info/licenses")):
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                yield "licenses/" + label + "/" + path.relative_to(folder).as_posix(), path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.2.2")
    args = parser.parse_args()
    if not all(char in "0123456789.-abcdefghijklmnopqrstuvwxyz" for char in args.version):
        parser.error("Use a simple release version")
    manifest = json.loads((ROOT / ".build/windows_payload/build-manifest.json").read_text())
    for relative, expected in manifest["source_files"].items():
        if digest(ROOT / relative) != expected:
            raise RuntimeError("Rebuild executable after application edits: " + relative)
    validation = json.loads((ROOT / ".build/release-validation.json").read_text())
    if validation.get("status") != "passed":
        raise RuntimeError("Required validation has not completed successfully")
    executable = DIST / "여비청구서변환기.exe"
    validation["version"] = args.version
    validation["exe_sha256"] = digest(executable)
    (DIST / "검증결과.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2))
    copies = {
        ROOT / "docs/사용설명서.md": "사용방법.txt",
        DOWNLOADS / "example-capture.png": "가명_테스트캡처.png",
        DOWNLOADS / "example-statement.pdf": "예시_여비지급명세서_검토용.pdf",
        DOWNLOADS / "contest-proposal.pdf": "공모전_제안서_초안.pdf",
        DOWNLOADS / "contest-proposal.docx": "공모전_제안서_초안.docx",
        DOWNLOADS / "contest-proposal.txt": "공모전_제안서_초안.txt",
        DOWNLOADS / "app-screenshot.png": "프로그램_화면.png",
    }
    for original, name in copies.items():
        if original.suffix == ".md":
            (DIST / name).write_text(original.read_text(), encoding="utf-8-sig")
        else:
            shutil.copyfile(original, DIST / name)
    (DIST / "처음읽기.txt").write_text(
        "여비청구서 변환기 v" + args.version + " — Windows 10/11 64비트\n\n"
        "기존 프로그램은 종료한 후 새 ZIP을 별도 폴더에 풀어 실행하세요.\n"
        "1. 여비청구서변환기.exe를 실행합니다.\n"
        "2. 캡처를 Ctrl+V로 붙여넣거나 이미지 파일을 선택합니다.\n"
        "   출장 월별집계 텍스트 PDF도 보조 입력으로 선택할 수 있습니다.\n"
        "3. 원본과 이름·실제 시각·차량·지급대상을 대조해 수정합니다.\n"
        "4. 기관 지급기준을 확인하고 일비 다시 계산 후 PDF를 저장합니다.\n"
        "5. 작업이 끝나면 프로그램 화면의 종료를 누릅니다.\n\n"
        "이미지·PDF는 외부 서버로 보내지 않습니다. Python 설치는 필요하지 않습니다.\n"
        "월별집계 PDF는 시스템의 결재완료 내역으로 처리합니다.\n"
        "캡처의 열 인식이 실패하면 표 배치 직접 확인으로 경계와 행 수를 확인하세요.\n"
        "미확인 내용이나 지급기준 확인이 남으면 검토용으로 저장합니다.\n"
        "관외·여러 날 출장은 자동 계산 범위 밖이므로 별도 검토가 필요합니다.\n\n"
        "DOCX와 TXT 제안서는 복사·수정용 초안입니다.\n"
        "개인 참가정보·동의·서명은 직접 작성하세요.\n"
        "오류가 나면 화면의 오류 진단 정보에서 진단 정보 복사를 눌러 문의하세요.\n"
        "상세 사용법: 사용방법.txt / 소스·빌드 절차: source.zip\n"
        "예시 이미지와 예시 명세서는 가명 자료입니다.\n",
        encoding="utf-8-sig")
    with zipfile.ZipFile(DIST / "source.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in source_files():
            archive.write(path, "travel-claim/" + path.relative_to(ROOT).as_posix())
    files = ["여비청구서변환기.exe", "처음읽기.txt", "사용방법.txt", "가명_테스트캡처.png",
             "예시_여비지급명세서_검토용.pdf", "공모전_제안서_초안.pdf", "공모전_제안서_초안.docx",
             "공모전_제안서_초안.txt", "프로그램_화면.png", "검증결과.json", "source.zip"]
    output = DIST / ("TravelClaim-Windows-v" + args.version + ".zip")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in files:
            archive.write(DIST / name, name)
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            archive.write(ROOT / name, name)
        for name, path in licenses():
            archive.write(path, name)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Release ZIP failed integrity verification")
        if hashlib.sha256(archive.read("여비청구서변환기.exe")).hexdigest() != validation["exe_sha256"]:
            raise RuntimeError("Packaged executable checksum mismatch")
    if output.stat().st_size >= 100 * 1024 * 1024:
        raise RuntimeError("Release ZIP exceeds GitHub's per-file size limit")
    shutil.copyfile(output, DOWNLOADS / output.name)
    shutil.copyfile(output, DOWNLOADS / "TravelClaim-Windows.zip")
    shutil.copyfile(DIST / "사용방법.txt", DOWNLOADS / "USER-GUIDE.txt")
    checksums = []
    for path in sorted(DOWNLOADS.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            checksums.append(digest(path) + "  " + path.name)
    (DOWNLOADS / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n")
    print(output)
    print("Bytes:", output.stat().st_size)
    print("SHA256:", digest(output))


if __name__ == "__main__":
    main()
