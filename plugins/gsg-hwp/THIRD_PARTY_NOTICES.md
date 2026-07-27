# Third-Party Notices

This document covers every direct runtime and test dependency declared in
`pyproject.toml`. It does not enumerate transitive dependencies. License
identifiers were checked on 2026-07-26 against the metadata and license files
installed from each exact pinned PyPI release.

## Runtime dependencies

| Package | License | Verification |
|---|---|---|
| [`mcp==1.26.0`](https://pypi.org/project/mcp/1.26.0/) | MIT | Wheel metadata and `LICENSE` |
| [`pdfplumber==0.11.9`](https://pypi.org/project/pdfplumber/0.11.9/) | MIT | Wheel classifier and `LICENSE.txt` |
| [`pydantic==2.12.5`](https://pypi.org/project/pydantic/2.12.5/) | MIT | `License-Expression` and `LICENSE` |
| [`pyhwpx==1.6.6`](https://pypi.org/project/pyhwpx/1.6.6/) | MIT | Wheel `license.txt` (MIT permission text) |
| [`pywin32==312`](https://pypi.org/project/pywin32/312/) | PSF-2.0 | Wheel metadata and PyPI classifier; see component note below |
| [`Pillow==12.2.0`](https://pypi.org/project/pillow/12.2.0/) | MIT-CMU | `License-Expression` and `LICENSE`; see component note below |
| [`opencv-contrib-python-headless==4.12.0.88`](https://pypi.org/project/opencv-contrib-python-headless/4.12.0.88/) | Apache-2.0 | Wheel metadata and `LICENSE.txt`; see component note below |
| [`typer==0.23.1`](https://pypi.org/project/typer/0.23.1/) | MIT | `License-Expression` and `LICENSE` |
| [`rich==14.3.2`](https://pypi.org/project/rich/14.3.2/) | MIT | Wheel metadata and `LICENSE` |
| [`reportlab==4.4.9`](https://pypi.org/project/reportlab/4.4.9/) | BSD-3-Clause | Wheel `LICENSE` (three-clause BSD terms) |
| [`pypdf==6.10.0`](https://pypi.org/project/pypdf/6.10.0/) | BSD-3-Clause | `License-Expression` and `LICENSE` |
| [`pymupdf==1.28.0`](https://pypi.org/project/pymupdf/1.28.0/) | AGPL-3.0, or an Artifex commercial license | Wheel metadata and `COPYING`; see the dedicated notice below |

`pywin32` contains separately licensed components. Its wheel includes, among
other notices, LGPL-2.1 terms for `adodbapi`, BSD-style terms for core Win32 and
COM portions, MIT terms for the MAPI stub library, the Scintilla license, and
the Python license. Pillow and the OpenCV wheel likewise include their own
third-party component notices. If a Python package is redistributed, retain
the complete license and third-party notice files shipped in that package.

## Optional test dependencies

| Package | License | Verification |
|---|---|---|
| [`basedpyright==1.39.8`](https://pypi.org/project/basedpyright/1.39.8/) | MIT | Wheel classifier and `LICENSE.txt` |
| [`pytest==8.4.2`](https://pypi.org/project/pytest/8.4.2/) | MIT | Wheel metadata and `LICENSE` |
| [`ruff==0.15.22`](https://pypi.org/project/ruff/0.15.22/) | MIT | `License-Expression` and `LICENSE` |

## PyMuPDF

- Package: `pymupdf==1.28.0`
- Copyright holder and licensor: Artifex Software, Inc.
- License: GNU Affero General Public License v3.0 (AGPL-3.0), or an
  Artifex commercial license

**배포 형태:** 이 저장소는 PyMuPDF를 포함하지 않으며 설치 시 `uv sync`가
PyPI에서 직접 내려받는다.

The current release layout does not bundle Python packages. The repository
tracks no wheel, package archive, `.venv`, `site-packages`, or PyMuPDF payload;
`.venv` is ignored. `runtime/bootstrap_runtime.ps1` recreates the environment
and installs the runtime declarations from `pyproject.toml`, using the package
index by default. An operator may instead supply an external wheelhouse for an
offline installation; that wheelhouse is not part of this repository.
