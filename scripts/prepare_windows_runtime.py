#!/usr/bin/env python3
"""Prepare only the verified Microsoft C++ DLLs needed by the PDF engine.

The package is pinned to conda-forge's win-64 vc14_runtime build and checked
before extraction. Only the three imported runtime DLLs and their Microsoft
license files are redistributed. zstandard is a build dependency, not part of
the application runtime.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".build"
VC_RUNTIME = {
    "name": "vc14_runtime",
    "version": "14.51.36247",
    "filename": "vc14_runtime-14.51.36247-habf1de7_41.conda",
    "url": "https://conda.anaconda.org/conda-forge/win-64/vc14_runtime-14.51.36247-habf1de7_41.conda",
    "sha256": "4e4cb599cdc41bf2109d1464c127b5bcbddf548ce3e322e612afb691338b48f8",
    "source": "conda-forge/win-64 current_repodata.json, pinned 2026-10-22",
    "license": "LicenseRef-MicrosoftVisualCpp2015-2022Runtime",
}
REQUIRED_DLLS = {"msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verified_archive() -> Path:
    target = CACHE / "downloads" / VC_RUNTIME["filename"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        temporary = target.with_suffix(target.suffix + ".part")
        try:
            with urllib.request.urlopen(VC_RUNTIME["url"], timeout=120) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
            if sha256(temporary) != VC_RUNTIME["sha256"]:
                raise RuntimeError("Microsoft runtime archive checksum mismatch")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    if sha256(target) != VC_RUNTIME["sha256"]:
        raise RuntimeError("Cached Microsoft runtime archive checksum mismatch; remove it and retry")
    return target


def _extract_members(reader, destination: Path) -> None:
    with tarfile.open(fileobj=reader, mode="r|") as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            if not parts or ".." in parts or member.name.startswith("/") or any(":" in part for part in parts):
                raise RuntimeError("Unsafe Microsoft runtime package member")
            if not member.isfile():
                continue
            name = parts[-1].lower()
            target = None
            if name in REQUIRED_DLLS and (len(parts) == 1 or parts[:2] == ("Library", "bin")):
                target = destination / name
            elif parts[:2] == ("info", "licenses") and len(parts) >= 3:
                target = destination.joinpath("licenses", *parts[2:])
            if target is None:
                continue
            stream = archive.extractfile(member)
            if stream is None:
                raise RuntimeError("Unreadable Microsoft runtime package member")
            content = stream.read()
            if target.is_file() and target.read_bytes() != content:
                raise RuntimeError("Conflicting Microsoft runtime package file")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)


def prepare_runtime() -> Path:
    try:
        import zstandard
    except ImportError as exc:
        raise RuntimeError("Build dependency missing: python -m pip install zstandard==0.25.0") from exc
    package = verified_archive()
    staging = CACHE / "windows_vc_runtime-staging"
    final = CACHE / "windows_vc_runtime"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    with zipfile.ZipFile(package) as archive:
        for name in archive.namelist():
            if name.startswith(("pkg-", "info-")) and name.endswith(".tar.zst"):
                with archive.open(name) as compressed, zstandard.ZstdDecompressor().stream_reader(compressed) as reader:
                    _extract_members(reader, staging)
    for name in REQUIRED_DLLS:
        path = staging / name
        if not path.is_file() or path.read_bytes()[:2] != b"MZ":
            raise RuntimeError("Microsoft runtime package lacks required Windows DLL: " + name)
    if not any((staging / "licenses").rglob("*")):
        raise RuntimeError("Microsoft runtime license files are missing")
    manifest = {"package": VC_RUNTIME, "files": {name: sha256(staging / name) for name in sorted(REQUIRED_DLLS)}}
    (staging / "BUILD-MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if final.exists():
        shutil.rmtree(final)
    staging.replace(final)
    print("Verified Microsoft C++ PDF runtime (3 DLLs)", flush=True)
    return final


if __name__ == "__main__":
    print(prepare_runtime())
