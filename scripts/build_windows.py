#!/usr/bin/env python3
"""Build the portable Windows x64 application without requiring Windows.

Downloads remain TLS verified. Archives are checked against hashes published by
NuGet (Python), PyPI (PDF libraries), and conda-forge (Microsoft runtime) before use. No uploaded documents
are included. Only the app/ source tree and explicitly prepared runtimes ship.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile

from prepare_windows_runtime import VC_RUNTIME, prepare_runtime

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".build"
DOWNLOADS = CACHE / "downloads"
STAGE = CACHE / "windows_payload"
PORTABLE = ROOT / "dist" / "TravelClaim-Portable"
START_NAME = "여비청구서 시작.cmd"
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


def copy_app() -> None:
    app = ROOT / "app"
    for required in [app / "backend.py", app / "index.html"]:
        if not required.is_file():
            raise RuntimeError(f"Required application file does not exist: {required}")
    shutil.copytree(
        app, STAGE / "app",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store", "ocr", "ocr_parser.py"),
    )


def add_windows_runtime(runtime: Path) -> None:
    # App-local DLLs keep the portable PDF engine independent of an installed
    # C++ redistributable. Windows 10/11 supplies the Universal C Runtime.
    for library in runtime.glob("*.dll"):
        shutil.copy2(library, STAGE / "python" / "Lib" / "site-packages" / "pymupdf" / library.name)
    shutil.copytree(runtime / "licenses", STAGE / "licenses" / "WindowsVC")
    shutil.copy2(runtime / "BUILD-MANIFEST.json", STAGE / "licenses" / "WindowsVC" / "BUILD-MANIFEST.json")


def write_start_script() -> None:
    # All commands are plain text. The runtime is already present in the ZIP;
    # this script does not extract, install, elevate, hide or copy executables.
    lines = [
        "@echo off",
        "setlocal EnableExtensions DisableDelayedExpansion",
        'pushd "%~dp0"',
        "if errorlevel 1 (",
        "  echo Cannot open the program folder.",
        "  pause",
        "  exit /b 1",
        ")",
        'if not exist "python\\python.exe" (',
        "  echo Python runtime is missing. Extract the whole ZIP into one folder.",
        "  popd",
        "  pause",
        "  exit /b 1",
        ")",
        '"python\\python.exe" -B -X utf8 -E -s "app\\backend.py" --desktop',
        'set "TRAVELCLAIM_EXIT_CODE=%ERRORLEVEL%"',
        "popd",
        'if not "%TRAVELCLAIM_EXIT_CODE%"=="0" (',
        "  echo The application stopped with an error. Read the console details above.",
        "  pause",
        ")",
        "exit /b %TRAVELCLAIM_EXIT_CODE%",
    ]
    (STAGE / START_NAME).write_bytes(("\r\n".join(lines) + "\r\n").encode("ascii"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true", help="Verify Python, PyMuPDF, fontTools and Microsoft C++ runtime archives")
    args = parser.parse_args()
    archives = {"python": download(PYTHON), "pymupdf": download(PYMUPDF), "fonttools": download(FONTTOOLS)}
    windows_runtime = prepare_runtime()
    if args.prepare_only:
        print("Verified portable-folder prerequisites; no custom launcher compilation is used.")
        return
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    safe_zip_extract(archives["python"], STAGE / "python", "tools/")
    # Retain the official stdlib needed by the local HTTP interface; omit only
    # development headers, static libraries and command-line packaging tools.
    for path in [STAGE / "python" / "include", STAGE / "python" / "libs", STAGE / "python" / "Tools", STAGE / "python" / "Scripts"]:
        if path.exists():
            shutil.rmtree(path)
    safe_zip_extract(archives["pymupdf"], STAGE / "python" / "Lib" / "site-packages")
    safe_zip_extract(archives["fonttools"], STAGE / "python" / "Lib" / "site-packages")
    copy_app()
    add_windows_runtime(windows_runtime)
    write_start_script()
    files = {path.relative_to(STAGE).as_posix(): sha256(path)
             for path in sorted(STAGE.rglob("*")) if path.is_file()}
    manifest = {
        "platform": "windows-amd64", "distribution": "portable-folder", "entrypoint": START_NAME,
        "python": PYTHON, "pymupdf": PYMUPDF, "fonttools": FONTTOOLS, "vc_runtime": VC_RUNTIME,
        "source_files": {name: digest for name, digest in files.items() if name.startswith("app/")},
        "runtime_files": files,
        "pe_files": {name: digest for name, digest in files.items() if Path(name).suffix.lower() in (".exe", ".dll", ".pyd")},
    }
    (STAGE / "build-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for relative, expected in manifest["source_files"].items():
        current = ROOT / relative
        if not current.is_file() or sha256(current) != expected:
            raise RuntimeError(f"Application source changed during preparation: {relative}. Rebuild after edits finish.")
    if PORTABLE.exists():
        shutil.rmtree(PORTABLE)
    shutil.copytree(STAGE, PORTABLE)
    print(f"Prepared {PORTABLE}")
    print(f"Entrypoint: {START_NAME} (plain CMD; official Python runtime)")
    print(f"Runtime files: {len(files)}; PE files: {len(manifest['pe_files'])}")
    print(f"Python SHA256: {sha256(PORTABLE / 'python/python.exe')}")


if __name__ == "__main__":
    main()
