#!/usr/bin/env python3
"""Prepare offline Korean/English OCR assets without system installation.

Run from any directory with Python 3.10+ and zstandard==0.25.0 installed:
  python scripts/prepare_ocr.py
  python scripts/prepare_ocr.py --models-only --linux-smoke

Windows artifacts are pinned conda-forge win-64 packages. Each package is
verified against its SHA256 from HTTPS conda-forge current_repodata.json,
observed 2026-10-22. Only runtime DLLs, tesseract.exe, configuration files,
and their licenses are redistributed. Tesseract models come from an exact
upstream tessdata_fast commit and are checked against pinned SHA256 values.
No network access is required when using the resulting application.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".build" / "ocr_windows"
RUNTIME = CACHE / "runtime"
MODELS = ROOT / "app" / "ocr" / "tessdata"
MODEL_COMMIT = "87416418657359cb625c412a48b6e1d6d41c29bd"
MODEL_HASHES = {
    "kor": "6b85e11d9bbf07863b97b3523b1b112844c43e713df8b66418a081fd1060b3b2",
    "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
}
# Complete transitive dependency closure; LGPL libarchive variant avoids LZO.
PACKAGES = [{'name': 'tesseract',
  'version': '5.5.3',
  'filename': 'tesseract-5.5.3-he87eeb8_0.conda',
  'sha256': 'edbe2455a8ee83c47c04bc93d6b75957b9f76b194f0ac2fe69f31ab46de7e024',
  'license': 'Apache-2.0'},
 {'name': 'vc',
  'version': '14.5',
  'filename': 'vc-14.5-ha367084_41.conda',
  'sha256': '35444c55a92e2f7f7ba26bc70f81e56e52344f7d064c0fd4b40a46a58517b79c',
  'license': 'BSD-3-Clause'},
 {'name': 'vc14_runtime',
  'version': '14.51.36247',
  'filename': 'vc14_runtime-14.51.36247-habf1de7_41.conda',
  'sha256': '4e4cb599cdc41bf2109d1464c127b5bcbddf548ce3e322e612afb691338b48f8',
  'license': 'LicenseRef-MicrosoftVisualCpp2015-2022Runtime'},
 {'name': 'ucrt',
  'version': '10.0.26100.0',
  'filename': 'ucrt-10.0.26100.0-h57928b3_0.conda',
  'sha256': '3005729dce6f3d3f5ec91dfc49fc75a0095f9cd23bab49efb899657297ac91a5',
  'license': 'LicenseRef-MicrosoftWindowsSDK10'},
 {'name': 'giflib',
  'version': '5.2.2',
  'filename': 'giflib-5.2.2-hb0bd012_1.conda',
  'sha256': '573af9bb6294d125a0be6dc4963e589188da92426c3887feb60dd22a07530e4f',
  'license': 'MIT'},
 {'name': 'libwebp-base',
  'version': '1.6.0',
  'filename': 'libwebp-base-1.6.0-h4d5522a_1.conda',
  'sha256': '4470d98d3178b0d45492eed4808afe421d2e7808352b8d06c2dc29166b75d94d',
  'license': 'BSD-3-Clause'},
 {'name': 'libpng',
  'version': '1.6.59',
  'filename': 'libpng-1.6.59-hdc8cecf_0.conda',
  'sha256': '36050852deae6edc98d7b71489b8d07254e2753c69bd524bf5784927cd1f0b2c',
  'license': 'zlib-acknowledgement'},
 {'name': 'openjpeg',
  'version': '2.5.4',
  'filename': 'openjpeg-2.5.4-h90fa87c_2.conda',
  'sha256': 'd1c630ccd9ae0898b48d84e986f4c330f66a25e535f99efe8cbf03f042b33da5',
  'license': 'BSD-2-Clause'},
 {'name': 'liblzma',
  'version': '5.8.3',
  'filename': 'liblzma-5.8.3-hfd05255_1.conda',
  'sha256': 'd36c4a1e1f80fd08e18a407e03622ff2f34dfdd022da6488ad19603dea19e6d5',
  'license': '0BSD'},
 {'name': 'leptonica',
  'version': '1.87.0',
  'filename': 'leptonica-1.87.0-hb83fb89_1.conda',
  'sha256': '6a0e2e099c4b530300fdcc092ddd0ffcc5383dbd04cdf03b232996857b3017d8',
  'license': 'BSD-2-Clause'},
 {'name': 'libtiff',
  'version': '4.7.2',
  'filename': 'libtiff-4.7.2-h8f73337_1.conda',
  'sha256': '3575a092e3e52625a1767804a4ebd321becaadf61b63dcd6735ebb88c0b3c359',
  'license': 'HPND'},
 {'name': 'libjpeg-turbo',
  'version': '3.2.0',
  'filename': 'libjpeg-turbo-3.2.0-hfd05255_1.conda',
  'sha256': 'df78ab4c0eecb3dd9331898f96baeed8e5ca1c363346332517abeb0b614b9a53',
  'license': 'IJG AND BSD-3-Clause AND Zlib'},
 {'name': 'libzlib',
  'version': '1.3.2',
  'filename': 'libzlib-1.3.2-hfd05255_3.conda',
  'sha256': '0629c2cc0404d3bb29d6baa7b4ba62da80797015e86de050db81ea5a07050527',
  'license': 'Zlib'},
 {'name': 'libcurl',
  'version': '8.23.0',
  'filename': 'libcurl-8.23.0-hdb0ef4a_0.conda',
  'sha256': 'd3088e1fd049fe0c1905ba6174e77279ddb4817739a204aaf4bd31d59d38ea6a',
  'license': 'curl'},
 {'name': 'vcomp14',
  'version': '14.51.36247',
  'filename': 'vcomp14-14.51.36247-habf1de7_41.conda',
  'sha256': '731e043390c9457299484d39e427221fc868a9249540a498a5a4f6456c7744d1',
  'license': 'LicenseRef-MicrosoftVisualCpp2015-2022Runtime'},
 {'name': 'bzip2',
  'version': '1.0.8',
  'filename': 'bzip2-1.0.8-h0ad9c76_10.conda',
  'sha256': '04767466ee9227c9c57ab2c6503e0149177d34111c7418d2f420297acb1eb229',
  'license': 'bzip2-1.0.6'},
 {'name': 'libxml2',
  'version': '2.15.4',
  'filename': 'libxml2-2.15.4-he095d88_1.conda',
  'sha256': '81e8cace5c3cb62c4125882b4a6df33ab48c16349231d8ea5008bbe0ee93c10f',
  'license': 'MIT'},
 {'name': 'libxml2-16',
  'version': '2.15.4',
  'filename': 'libxml2-16-2.15.4-h3cfd58e_1.conda',
  'sha256': '819c4edd7b7bcfa9461aeccb56c0a7e9fa7c251163333e64222c142499822958',
  'license': 'MIT'},
 {'name': 'lz4-c',
  'version': '1.10.0',
  'filename': 'lz4-c-1.10.0-h6a83c73_2.conda',
  'sha256': '6824e36be0f95ae516dc36dddd18f69cbad0e4e07906fcb10a5f26a8dc471903',
  'license': 'BSD-2-Clause'},
 {'name': 'openssl',
  'version': '3.6.5',
  'filename': 'openssl-3.6.5-hf411b9b_0.conda',
  'sha256': 'bc44625e0c6d83f3e02b5f6adc01e0e3ca7001d3d94e24959591a57f5a011b9a',
  'license': 'Apache-2.0'},
 {'name': 'zstd',
  'version': '1.5.7',
  'filename': 'zstd-1.5.7-h534d264_7.conda',
  'sha256': 'ca7daae4f218a11fab82cc2857f0ea518ec3f46acec60490485347a4c22c6b3e',
  'license': 'BSD-3-Clause'},
 {'name': 'lerc',
  'version': '4.2.0',
  'filename': 'lerc-4.2.0-hd936e49_0.conda',
  'sha256': '93d666f63f284ef77b87b0b1f77b70f7d36d315a132f9afa64bc0012d937ba39',
  'license': 'Apache-2.0'},
 {'name': 'libdeflate',
  'version': '1.25',
  'filename': 'libdeflate-1.25-h1a1d4e4_1.conda',
  'sha256': 'af1cda21d4653f594fbef20aa4e1ff158a546902b3307ef8af9a9b44b43862d2',
  'license': 'MIT'},
 {'name': 'krb5',
  'version': '1.22.2',
  'filename': 'krb5-1.22.2-h719d79b_2.conda',
  'sha256': '63ff03324e903eb01a715ccf357df56d66224e61952fd6615d86490ebefb3285',
  'license': 'MIT'},
 {'name': 'libpsl',
  'version': '0.23.1',
  'filename': 'libpsl-0.23.1-h9b16d47_1.conda',
  'sha256': 'e53fe3b09f82b59b644264133d6d148ac31bb5451b7583cff55690bf038f2f62',
  'license': 'MIT'},
 {'name': 'libssh2',
  'version': '1.11.1',
  'filename': 'libssh2-1.11.1-h734d217_1.conda',
  'sha256': '1097b08429b4f53f5751a32c6d99a083d227ca7f7812c0b239eea124cec073a0',
  'license': 'BSD-3-Clause'},
 {'name': 'icu',
  'version': '78.3',
  'filename': 'icu-78.3-h5112557_2.conda',
  'sha256': '75c549b55b673e15de8785a8e5dd85bca7eb612eee0ff4dc8d7bdaa15eacbdbb',
  'license': 'MIT'},
 {'name': 'libiconv',
  'version': '1.18',
  'filename': 'libiconv-1.18-hc1393d2_3.conda',
  'sha256': '35e04e3ddac7720fc7c550a1c9f998604299d6a7bd1f3ec9b2825d825061daa2',
  'license': 'LGPL-2.1-only'},
 {'name': 'ca-certificates',
  'version': '2025.1.31',
  'filename': 'ca-certificates-2025.1.31-h56e8100_0.conda',
  'sha256': '1bedccdf25a3bd782d6b0e57ddd97cdcda5501716009f2de4479a779221df155',
  'license': 'ISC'},
 {'name': 'libarchive',
  'version': '3.8.9',
  'filename': 'libarchive-3.8.9-lgpl_hb42df8c_1.conda',
  'sha256': 'b99265b28739c9d10bad5c2396c07d6796297d774d31e58854447e952f780bcc',
  'license': 'BSD-2-Clause'}]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, path: Path, expected: str) -> Path:
    if path.is_file() and sha256(path) == expected:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": "TravelClaimOCR-build/1.0"})
    try:
        # urllib keeps inherited HTTPS proxy and certificate verification.
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as out:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != expected:
            raise RuntimeError(f"SHA256 mismatch: {path.name}")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def prepare_models() -> None:
    for language, digest in MODEL_HASHES.items():
        url = f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{MODEL_COMMIT}/{language}.traineddata"
        download(url, MODELS / f"{language}.traineddata", digest)
    # Apache-2.0 is the upstream model repository license.
    license_url = f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{MODEL_COMMIT}/LICENSE"
    license_path = MODELS.parent / "TESSDATA-LICENSE.txt"
    if not license_path.exists():
        with urllib.request.urlopen(license_url, timeout=60) as response:
            license_path.write_bytes(response.read())
    print("Korean/English OCR models verified", flush=True)


def extract_tar(stream, package: dict, runtime: Path) -> None:
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            if not member.isfile():
                continue
            parts = PurePosixPath(member.name).parts
            if ".." in parts or not parts or member.name.startswith("/"):
                raise RuntimeError(f"Unsafe package path: {member.name}")
            target = None
            if len(parts) >= 3 and parts[:2] == ("Library", "bin"):
                if member.name.lower().endswith(".dll") or parts[-1].lower() == "tesseract.exe":
                    target = runtime / parts[-1]
            elif len(parts) >= 5 and parts[:3] == ("Library", "share", "tessdata"):
                if parts[3] in {"configs", "tessconfigs"}:
                    target = runtime.joinpath("tessdata", *parts[3:])
            elif len(parts) >= 3 and parts[:2] == ("info", "licenses"):
                target = runtime.joinpath("licenses", package["name"], *parts[2:])
            if target is None:
                continue
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError(f"Unreadable package member: {member.name}")
            data = source.read()
            if target.is_file() and target.read_bytes() != data:
                raise RuntimeError(f"Conflicting runtime file: {target.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


def extract_package(package: dict, runtime: Path) -> None:
    filename = package["filename"]
    path = CACHE / "downloads" / filename
    download("https://conda.anaconda.org/conda-forge/win-64/" + filename,
             path, package["sha256"])
    if filename.endswith(".conda"):
        try:
            import zstandard
        except ImportError as exc:
            raise RuntimeError("Build dependency missing: python -m pip install zstandard==0.25.0") from exc
        with zipfile.ZipFile(path) as zipped:
            for name in zipped.namelist():
                if name.startswith(("pkg-", "info-")) and name.endswith(".tar.zst"):
                    with zipped.open(name) as compressed:
                        with zstandard.ZstdDecompressor().stream_reader(compressed) as reader:
                            extract_tar(reader, package, runtime)
    else:
        with path.open("rb") as stream:
            extract_tar(stream, package, runtime)
    print("Verified and extracted " + package["name"], flush=True)


def prepare_windows() -> None:
    # Build into a fresh staging directory so no stale DLL can enter a release.
    staging = CACHE / "runtime-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    # Parallel downloads; extraction stays serial to detect filename conflicts.
    def fetch(package):
        return download("https://conda.anaconda.org/conda-forge/win-64/" + package["filename"],
                        CACHE / "downloads" / package["filename"], package["sha256"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, PACKAGES))
    for package in PACKAGES:
        extract_package(package, staging)
    tessdata = staging / "tessdata"
    tessdata.mkdir(exist_ok=True)
    for language in MODEL_HASHES:
        shutil.copy2(MODELS / f"{language}.traineddata", tessdata)
    shutil.copy2(MODELS.parent / "TESSDATA-LICENSE.txt", staging / "TESSDATA-LICENSE.txt")
    # The TSV command normally loads tessdata/configs/tsv. Keep an explicit
    # config even on upstream packages that omitted data directories.
    config = tessdata / "configs" / "tsv"
    config.parent.mkdir(parents=True, exist_ok=True)
    if not config.exists():
        config.write_text("tessedit_create_tsv 1\n", encoding="ascii")
    if not (staging / "tesseract.exe").is_file():
        raise RuntimeError("Windows package did not supply tesseract.exe")
    for item in staging.glob("*.dll"):
        if item.read_bytes()[:2] != b"MZ":
            raise RuntimeError(f"Not a Windows PE DLL: {item.name}")
    manifest = {"source": "conda-forge/win-64", "models_commit": MODEL_COMMIT,
                "model_sha256": MODEL_HASHES, "packages": PACKAGES,
                "windows_execution_tested": False}
    (staging / "BUILD-MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    staging.replace(RUNTIME)
    print(f"Windows OCR prepared: {RUNTIME}", flush=True)
    print("Windows runtime has not been executed on this Linux host.", flush=True)


def linux_smoke() -> None:
    from PIL import Image, ImageDraw, ImageFont
    tesseract = shutil.which("tesseract")
    if not tesseract:
        raise RuntimeError("Linux tesseract executable is required for --linux-smoke")
    cache = ROOT / ".build" / "ocr_linux"
    cache.mkdir(parents=True, exist_ok=True)
    fonts = [ROOT / "app" / "assets" / "NotoSansKR-Regular.ttf",
             Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")]
    font_path = next((path for path in fonts if path.is_file()), None)
    if not font_path:
        raise RuntimeError("No Korean font found for synthetic OCR smoke image")
    font = ImageFont.truetype(str(font_path), 38)
    image = Image.new("RGB", (1600, 260), "white")
    draw = ImageDraw.Draw(image)
    sample = ["근무지내 출장처리 목록", "출장기간 2026-10-21 09:00 ~ 2026-10-21 14:10", "출장목적 농업인 현장 점검"]
    for index, line in enumerate(sample):
        draw.text((36, 22 + index * 74), line, font=font, fill="black")
    image_path = cache / "korean-smoke.png"
    image.save(image_path)
    result = subprocess.run([tesseract, str(image_path), "stdout", "--tessdata-dir", str(MODELS),
                             "-l", "kor+eng", "--psm", "6", "-c", "tessedit_create_tsv=1"],
                            capture_output=True, text=True, encoding="utf-8", check=True)
    (cache / "korean-smoke.tsv").write_text(result.stdout, encoding="utf-8")
    words = " ".join(line.split("\t")[-1] for line in result.stdout.splitlines()[1:])
    # Korean OCR may emit individual Hangul syllables as separate TSV words.
    # Verify the decoded characters after joining token boundaries.
    normalized = "".join(words.split())
    for expected in ["출장", "2026-10-21", "09:00", "14:10"]:
        if expected not in normalized:
            raise RuntimeError(f"Korean OCR smoke test missed {expected!r}")
    print("Linux Korean OCR smoke test passed (synthetic image; not the user's screenshot)", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-only", action="store_true")
    parser.add_argument("--linux-smoke", action="store_true")
    args = parser.parse_args()
    prepare_models()
    if not args.models_only:
        prepare_windows()
    if args.linux_smoke:
        linux_smoke()


if __name__ == "__main__":
    main()
