# Third-party components

This distribution includes the following components. Their licenses and copyright notices are retained in the bundled runtime. The application source and reproducible build scripts are provided alongside the executable.

| Component | Version / source | License |
|---|---|---|
| Python | 3.12.10, official NuGet `python` package | PSF License and bundled notices |
| PyMuPDF / MuPDF | 1.26.6 | AGPL-3.0-or-later / bundled COPYING |
| fontTools | 4.61.1 | MIT / bundled LICENSE and LICENSE.external |
| Noto Sans CJK KR Regular | system Noto Sans CJK collection, Korean face | SIL Open Font License 1.1, app/fonts/LICENSE.txt |
| Go runtime | 1.25.3 | BSD-3-Clause |
| Microsoft Visual C++ runtime | 14.51.36247, pinned conda-forge win-64 `vc14_runtime` build | Microsoft Visual C++ 2015–2022 runtime license, bundled TXT/RTF/DOCX |

The three Microsoft DLLs required by the PDF engine (`msvcp140.dll`, `vcruntime140.dll`, and `vcruntime140_1.dll`) remain subject to their included license terms. Build metadata records download sources, archive checksums, and DLL SHA256 digests. `scripts/prepare_windows_runtime.py` reproduces this minimal dependency bundle. Windows 10/11 supplies the Universal C Runtime. The build dependency `zstandard==0.25.0` is used only to unpack the verified Microsoft runtime archive and is not included in the application.
