#!/usr/bin/env python3
"""Bundle a tested, transparent Windows portable folder with its source.

Run scripts/build_windows.py first, test that exact portable build, and record
the results in .build/release-validation.json. Validation must name its version
and the SHA256 of the tested build-manifest.json. Private attachments, caches,
legacy launchers, and unrelated dist/ files never enter the release.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
PORTABLE = DIST / "TravelClaim-Portable"
DOWNLOADS = ROOT / "downloads"
START_NAME = "여비청구서 시작.cmd"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(folder: Path) -> dict[str, str]:
    result = {}
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("Symlinks are not allowed in a release: " + str(path))
        if path.is_file():
            result[path.relative_to(folder).as_posix()] = digest(path)
    return result


def app_sources() -> dict[str, str]:
    result = {}
    for path in sorted((ROOT / "app").rglob("*")):
        relative = path.relative_to(ROOT / "app")
        if ("__pycache__" in relative.parts or "ocr" in relative.parts
                or path.suffix in (".pyc", ".pyo")
                or path.name in (".DS_Store", "ocr_parser.py")):
            continue
        if path.is_symlink():
            raise RuntimeError("Symlinks are not allowed in application source")
        if path.is_file():
            result["app/" + relative.as_posix()] = digest(path)
    return result


def source_files():
    # Include the source needed to rebuild the current portable distribution;
    # the retired Go/self-extracting launcher is intentionally not distributed.
    for folder in ("app", "scripts", "tests", "docs"):
        for path in sorted((ROOT / folder).rglob("*")):
            if (path.is_file() and "__pycache__" not in path.parts
                    and path.suffix not in (".pyc", ".pyo")
                    and path.name not in ("payload.zip", ".DS_Store", "ocr_parser.py")
                    and not (folder == "app" and "ocr" in path.relative_to(ROOT / folder).parts)):
                if path.is_symlink():
                    raise RuntimeError("Symlinks are not allowed in corresponding source")
                yield path
    for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "requirements.txt",
                 "build-requirements.txt", ".gitignore"):
        yield ROOT / name


def licenses():
    yield "licenses/Python/LICENSE.txt", PORTABLE / "python/LICENSE.txt"
    yield "licenses/PyMuPDF/COPYING", PORTABLE / "python/Lib/site-packages/pymupdf-1.26.6.dist-info/COPYING"
    yield "licenses/NotoSansCJK/LICENSE.txt", PORTABLE / "app/fonts/LICENSE.txt"
    folder = PORTABLE / "python/Lib/site-packages/fonttools-4.61.1.dist-info/licenses"
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            yield "licenses/fontTools/" + path.relative_to(folder).as_posix(), path
    # Microsoft runtime licenses are already part of the verified portable tree.


def checked_build(version: str) -> tuple[dict, dict]:
    manifest_path = PORTABLE / "build-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("distribution") != "portable-folder"
            or manifest.get("platform") != "windows-amd64"
            or manifest.get("entrypoint") != START_NAME):
        raise RuntimeError("Expected the transparent Windows portable-folder build")
    expected = manifest.get("runtime_files")
    if not isinstance(expected, dict) or START_NAME not in expected:
        raise RuntimeError("Build manifest does not cover the start script and runtime")
    for relative in expected:
        path = PurePosixPath(relative)
        if (path.is_absolute() or ".." in path.parts or "\\" in relative
                or ":" in relative or path.as_posix() != relative):
            raise RuntimeError("Unsafe build manifest path: " + relative)
    actual = inventory(PORTABLE)
    actual.pop("build-manifest.json", None)
    if actual != expected:
        raise RuntimeError("Portable files changed after building; rebuild and retest")
    if any(PurePosixPath(name).name in ("여비청구서변환기.exe", "payload.zip") for name in expected):
        raise RuntimeError("Retired self-extracting executable found in portable files")
    pe_files = {name: value for name, value in expected.items()
                if PurePosixPath(name).suffix.lower() in (".exe", ".dll", ".pyd")}
    if manifest.get("pe_files") != pe_files:
        raise RuntimeError("PE inventory does not match the portable files")
    source_hashes = {name: value for name, value in expected.items() if name.startswith("app/")}
    if manifest.get("source_files") != source_hashes or app_sources() != source_hashes:
        raise RuntimeError("Application source changed; rebuild and retest before packaging")
    tree = ast.parse((PORTABLE / "app/backend.py").read_text(encoding="utf-8"))
    versions = [node.value.value for node in tree.body
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                and any(isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in node.targets)]
    if versions != [version]:
        raise RuntimeError("Application version does not match the requested release")
    validation = json.loads((ROOT / ".build/release-validation.json").read_text(encoding="utf-8"))
    if (validation.get("status") != "passed" or validation.get("version") != version
            or validation.get("build_manifest_sha256") != digest(manifest_path)):
        raise RuntimeError("Validation must pass for this exact version and build manifest")
    return manifest, validation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.3.1")
    args = parser.parse_args()
    if not args.version or not all(char in "0123456789.-abcdefghijklmnopqrstuvwxyz" for char in args.version):
        parser.error("Use a simple release version")
    manifest, validation = checked_build(args.version)
    # Keep package extras separate so the tested portable runtime stays intact.
    release = DIST / ("TravelClaim-Package-v" + args.version)
    if release.exists():
        shutil.rmtree(release)
    shutil.copytree(PORTABLE, release)
    (release / "검증결과.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    copies = {
        ROOT / "docs/사용설명서.md": "사용방법.txt",
        DOWNLOADS / "example-input.pdf": "가명_출장내역_예시.pdf",
        DOWNLOADS / "example-statement.pdf": "예시_여비지급명세서.pdf",
        DOWNLOADS / "contest-proposal.pdf": "공모전_제안서_초안.pdf",
        DOWNLOADS / "contest-proposal.docx": "공모전_제안서_초안.docx",
        DOWNLOADS / "contest-proposal.txt": "공모전_제안서_초안.txt",
        DOWNLOADS / "app-screenshot.png": "프로그램_화면.png",
    }
    for original, name in copies.items():
        if original.suffix == ".md":
            (release / name).write_text(original.read_text(encoding="utf-8"), encoding="utf-8-sig")
        else:
            shutil.copyfile(original, release / name)
    (release / "처음읽기.txt").write_text(
        "여비청구서 변환기 v" + args.version + " — Windows 10/11 64비트\n\n"
        "v0.3.0의 단일 EXE에서 백신 탐지가 보고됐습니다. 기존 파일은 격리 상태를 유지하세요.\n"
        "이번 ZIP은 실행환경과 소스가 보이는 폴더 방식입니다. 알약 등 실제 백신의 탐지 해제는 확인하지 못했습니다.\n"
        "새 배포도 탐지되면 실행을 중단하세요. 백신 해제나 예외 등록으로 실행하지 마세요.\n\n"
        "1. 기존 프로그램을 종료하고 새 ZIP을 별도 폴더에 모두 풉니다.\n"
        "2. 여비청구서 시작.cmd를 실행합니다. Python 설치는 필요하지 않습니다.\n"
        "   python·app·licenses 폴더와 시작 파일을 함께 보관하세요.\n"
        "   실행 중 검은색 콘솔 창이 열려 있습니다. 작업하는 동안 닫지 마세요.\n"
        "3. 시스템에서 내려받은 개인별 출장내역 PDF를 선택합니다.\n"
        "4. 자동 계산된 내역을 확인하고 PDF 저장을 누릅니다.\n"
        "   필요한 내용은 화면에서 수정할 수 있고 지급기준 설정도 바꿀 수 있습니다.\n"
        "5. 작업이 끝나면 프로그램 화면의 종료를 누릅니다.\n\n"
        "PDF는 외부 서버로 보내지 않습니다.\n"
        "개인별 내역은 확인행의 실제 시각과 상태를 사용하며, 미전송 등은 기본 제외합니다.\n"
        "기존 월별집계 PDF에는 결재완료 내역만 내보낸다는 업무 규칙을 적용합니다.\n"
        "주의·누락 안내는 화면에서 확인하며, 출력물에는 검토 상태 문구를 넣지 않습니다.\n"
        "관외·여러 날 출장은 자동 계산 범위 밖이므로 별도 검토가 필요합니다.\n\n"
        "DOCX와 TXT 제안서는 복사·수정용 초안입니다.\n"
        "개인 참가정보·동의·서명은 직접 작성하세요.\n"
        "오류가 나면 화면의 오류 진단 정보에서 진단 정보 복사를 눌러 문의하세요.\n"
        "상세 사용법: 사용방법.txt / 소스·빌드 절차: source.zip\n"
        "구성 파일 해시와 원본 의존성: build-manifest.json / 검증 범위: 검증결과.json\n"
        "예시 입력 PDF와 예시 명세서는 가명 자료입니다.\n",
        encoding="utf-8-sig")
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copyfile(ROOT / name, release / name)
    for name, path in licenses():
        target = release / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    with zipfile.ZipFile(release / "source.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in source_files():
            relative = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            if relative.startswith("app/") and hashlib.sha256(data).hexdigest() != manifest["source_files"].get(relative):
                raise RuntimeError("Application source changed while packaging: " + relative)
            archive.writestr("travel-claim/" + relative, data)
    output = DIST / ("TravelClaim-Windows-v" + args.version + ".zip")
    temporary = output.with_suffix(".zip.part")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(release.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(release).as_posix())
    with zipfile.ZipFile(temporary) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Release ZIP failed integrity verification")
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("Duplicate files in release ZIP")
        for name, expected in manifest["runtime_files"].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise RuntimeError("Packaged runtime checksum mismatch: " + name)
        if hashlib.sha256(archive.read("build-manifest.json")).hexdigest() != validation["build_manifest_sha256"]:
            raise RuntimeError("Packaged build manifest does not match validation")
    if temporary.stat().st_size >= 100 * 1024 * 1024:
        raise RuntimeError("Release ZIP exceeds GitHub's per-file size limit")
    temporary.replace(output)
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(output, DOWNLOADS / output.name)
    shutil.copyfile(output, DOWNLOADS / "TravelClaim-Windows.zip")
    shutil.copyfile(release / "사용방법.txt", DOWNLOADS / "USER-GUIDE.txt")
    checksums = []
    for path in sorted(DOWNLOADS.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            checksums.append(digest(path) + "  " + path.name)
    (DOWNLOADS / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    print(output)
    print("Bytes:", output.stat().st_size)
    print("SHA256:", digest(output))


if __name__ == "__main__":
    main()
