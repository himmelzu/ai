#!/usr/bin/env python3
"""Build the portable Windows x64 application without requiring Windows.

Downloads remain TLS verified. Archives are checked against hashes published by
NuGet (Python), PyPI (PyMuPDF), and go.dev (Go) before use. No uploaded documents
are included. Only the app/ source tree and explicitly prepared runtimes ship.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".build"
DOWNLOADS = CACHE / "downloads"
STAGE = CACHE / "windows_payload"
GO_VERSION = "1.25.3"
PYTHON = {
    "url": "https://api.nuget.org/v3-flatcontainer/python/3.12.10/python.3.12.10.nupkg",
    "filename": "python.3.12.10.nupkg",
    "sha256": "0eb85c2dfccccf1b17352de4c397f69194035b7d37149eacc16f1147d93de3b8",
    "source": "NuGet python 3.12.10 catalog SHA512 (also verified during preparation)",
}
PYMUPDF = {
    "url": "https://files.pythonhosted.org/packages/f9/e8/989f4eaa369c7166dc24f0eaa3023f13788c40ff1b96701f7047421554a8/pymupdf-1.26.6-cp310-abi3-win_amd64.whl",
    "filename": "pymupdf-1.26.6-cp310-abi3-win_amd64.whl",
    "sha256": "ce02ca96ed0d1acfd00331a4d41a34c98584d034155b06fd4ec0f051718de7ba",
    "source": "https://pypi.org/pypi/pymupdf/1.26.6/json",
}
FONTTOOLS = {
    "url": "https://files.pythonhosted.org/packages/c7/4e/ce75a57ff3aebf6fc1f4e9d508b8e5810618a33d900ad6c19eb30b290b97/fonttools-4.61.1-py3-none-any.whl",
    "filename": "fonttools-4.61.1-py3-none-any.whl",
    "sha256": "17d2bf5d541add43822bcf0c43d7d847b160c9bb01d15d5007d84e2217aaa371",
    "source": "https://pypi.org/pypi/fonttools/4.61.1/json",
}
GO = {
    "url": f"https://go.dev/dl/go{GO_VERSION}.linux-amd64.tar.gz",
    "filename": f"go{GO_VERSION}.linux-amd64.tar.gz",
    "sha256": "0335f314b6e7bfe08c3d0cfaa7c19db961b7b99fb20be62b0a826c992ad14e0f",
    "source": "https://go.dev/dl/?mode=json&include=all",
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(spec: dict) -> Path:
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    path = DOWNLOADS / spec["filename"]
    if not path.exists():
        temporary = path.with_suffix(path.suffix + ".part")
        print(f"Downloading {spec['filename']}", flush=True)
        try:
            with urllib.request.urlopen(spec["url"], timeout=120) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
            if sha256(temporary) != spec["sha256"]:
                raise RuntimeError(f"Checksum mismatch: {spec['filename']}")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    if sha256(path) != spec["sha256"]:
        raise RuntimeError(f"Cached archive checksum mismatch; remove {path} and retry")
    print(f"Verified {path.name}", flush=True)
    return path


def safe_zip_extract(archive: Path, destination: Path, prefix: str = "") -> None:
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            if prefix and not member.filename.startswith(prefix):
                continue
            name = member.filename[len(prefix):]
            if not name:
                continue
            relative = Path(name.replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts or ":" in name:
                raise RuntimeError(f"Unsafe archive path: {name}")
            target = destination / relative
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)


def prepare_go(archive: Path) -> Path:
    location = CACHE / f"go-{GO_VERSION}"
    executable = location / "go" / "bin" / "go"
    if not executable.is_file():
        location.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:gz") as source:
            source.extractall(location, filter="data")
    return executable


def copy_app() -> None:
    app = ROOT / "app"
    for required in [app / "backend.py", app / "index.html"]:
        if not required.is_file():
            raise RuntimeError(f"Required application file does not exist: {required}")
    shutil.copytree(
        app, STAGE / "app",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store", "ocr"),
    )


def add_ocr() -> None:
    ocr = CACHE / "ocr_windows" / "runtime"
    if not (ocr / "tesseract.exe").is_file():
        raise RuntimeError("Offline Windows OCR is not prepared. Run scripts/prepare_ocr.py first.")
    shutil.copytree(ocr, STAGE / "ocr")
    # Python supplies VCRUNTIME140, while PyMuPDF additionally imports MSVCP140.
    # Use the verified conda VC redistributable downloaded for offline OCR.
    runtime = next((p for p in ocr.rglob("msvcp140.dll") if p.is_file()), None)
    if runtime is None:
        raise RuntimeError("Prepared OCR runtime lacks msvcp140.dll required by PyMuPDF")
    for library in runtime.parent.glob("*.dll"):
        if library.name.lower().startswith(("msvcp", "vcruntime", "concrt")):
            shutil.copy2(library, STAGE / "python" / "Lib" / "site-packages" / "pymupdf" / library.name)


def make_payload() -> Path:
    payload = ROOT / "launcher" / "payload.zip"
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(STAGE.rglob("*")):
            if path.is_file():
                name = path.relative_to(STAGE).as_posix()
                info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100600 << 16
                archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true", help="Download and verify Python, PyMuPDF, fontTools and Linux Go only")
    args = parser.parse_args()
    archives = {"python": download(PYTHON), "pymupdf": download(PYMUPDF), "fonttools": download(FONTTOOLS), "go": download(GO)}
    go = prepare_go(archives["go"])
    if args.prepare_only:
        print(f"Verified build prerequisites ready; Go: {go}")
        return
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    safe_zip_extract(archives["python"], STAGE / "python", "tools/")
    # NuGet's full stdlib is retained: the local UI needs socket, HTTP and SSL.
    # Developer-only headers, static libraries and pip are unnecessary at runtime.
    for path in [STAGE / "python" / "include", STAGE / "python" / "libs", STAGE / "python" / "Tools", STAGE / "python" / "Scripts"]:
        if path.exists():
            shutil.rmtree(path)
    safe_zip_extract(archives["pymupdf"], STAGE / "python" / "Lib" / "site-packages")
    safe_zip_extract(archives["fonttools"], STAGE / "python" / "Lib" / "site-packages")
    copy_app()
    add_ocr()
    manifest = {
        "platform": "windows-amd64", "python": PYTHON, "pymupdf": PYMUPDF,
        "fonttools": FONTTOOLS, "go": GO, "source_files": {},
    }
    for path in sorted((STAGE / "app").rglob("*")):
        if path.is_file():
            manifest["source_files"][path.relative_to(STAGE).as_posix()] = sha256(path)
    (STAGE / "build-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    payload = make_payload()
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    executable = dist / "여비청구서변환기.exe"
    candidate = CACHE / "windows-launcher.exe"
    environment = os.environ.copy()
    environment.update({
        "GOOS": "windows", "GOARCH": "amd64", "CGO_ENABLED": "0",
        "GOTOOLCHAIN": "local", "GOCACHE": str(CACHE / "go-cache"),
        "GOMODCACHE": str(CACHE / "go-mod-cache"),
    })
    subprocess.run([str(go), "build", "-trimpath", "-buildvcs=false", "-ldflags=-H=windowsgui -s -w", "-o", str(candidate), "."],
                   cwd=ROOT / "launcher", env=environment, check=True)
    for relative, expected in manifest["source_files"].items():
        current = ROOT / relative
        if not current.is_file() or sha256(current) != expected:
            raise RuntimeError(f"Application source changed during packaging: {relative}. Rebuild after edits finish.")
    candidate.replace(executable)
    print(f"Payload {payload.stat().st_size / 1024**2:.1f} MiB")
    print(f"Built {executable} ({executable.stat().st_size / 1024**2:.1f} MiB)")
    print(f"SHA256 {sha256(executable)}")


if __name__ == "__main__":
    main()
