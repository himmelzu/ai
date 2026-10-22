# Third-party components

This distribution includes the following components. Their licenses and copyright notices are retained in the bundled runtime. The application source and reproducible build scripts are provided alongside the executable.

| Component | Version / source | License |
|---|---|---|
| Python | 3.12.10, official NuGet `python` package | PSF License and bundled notices |
| PyMuPDF / MuPDF | 1.26.6 | AGPL-3.0-or-later / bundled COPYING |
| fontTools | 4.61.1 | MIT / bundled LICENSE and LICENSE.external |
| Tesseract | 5.5.3, conda-forge Windows runtime | Apache-2.0 |
| tessdata_fast Korean and English | upstream commit `87416418657359cb625c412a48b6e1d6d41c29bd` | Apache-2.0 |
| Noto Sans CJK KR Regular | system Noto Sans CJK collection, Korean face | SIL Open Font License 1.1, app/fonts/LICENSE.txt |
| Go runtime | 1.25.3 | BSD-3-Clause |
| OCR runtime libraries | exact package list in scripts/prepare_ocr.py and runtime build manifest | Per-package licenses retained in runtime/licenses |

The Windows Microsoft runtime components remain subject to their included license terms. Build metadata records download sources and SHA256 digests. The OCR runtime includes Leptonica and its image/compression libraries, among other transitive dependencies; the complete pinned package list is preserved by `scripts/prepare_ocr.py`.
