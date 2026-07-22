# 제3자 구성요소 및 권리 고지

GSG HWP의 자체 소스코드와 해당 소스에서 빌드한 실행 파일은 함께 제공되는
MIT License에 따라 배포됩니다. 아래 구성요소와 권리는 GSG HWP의 MIT
License로 다시 허가되지 않으며, 각각의 권리자와 라이선스 조건을 따릅니다.

## Python 직접 의존성

설치기는 `uv.lock`(`plugins/gsg-hwp/uv.lock`)에 기록된 공식 Python 배포 파일을 해시로
검증해 사용자 전용 환경에 설치합니다. 이 저장소는 해당 Python 패키지 배포
파일을 vendor 폴더로 포함하지 않고 설치 시 내려받습니다. 각 설치 패키지에는
원 라이선스 파일이 함께 제공됩니다.

| 패키지 | 고정 버전 | 라이선스 | 프로젝트 |
|---|---:|---|---|
| mcp | 1.26.0 | MIT | <https://github.com/modelcontextprotocol/python-sdk> |
| pdfplumber | 0.11.9 | MIT | <https://github.com/jsvine/pdfplumber> |
| pydantic | 2.12.5 | MIT | <https://github.com/pydantic/pydantic> |
| pyhwpx | 1.6.6 | MIT | <https://github.com/martiniifun/pyhwpx> |
| pywin32 | 312 | Python Software Foundation License | <https://github.com/mhammond/pywin32> |
| Pillow | 12.2.0 | MIT-CMU | <https://github.com/python-pillow/Pillow> |
| typer | 0.23.1 | MIT | <https://github.com/fastapi/typer> |
| rich | 14.3.2 | MIT | <https://github.com/Textualize/rich> |
| reportlab | 4.4.9 | BSD | <https://pypi.org/project/reportlab/> |
| pypdf | 6.10.0 | BSD-3-Clause | <https://github.com/py-pdf/pypdf> |

간접 의존성의 정확한 버전과 배포 파일 해시는 `uv.lock`에 기록되어 있으며,
각 패키지에 포함된 라이선스와 고지를 그대로 따릅니다.

## pyhwpx와 파일 경로 보안 모듈

GSG HWP는 `pyhwpx.Hwp`를 Python 한컴 조작 어댑터로 사용합니다. 또한 설치
과정에서 고정된 `pyhwpx==1.6.6` 배포 파일에 포함된
`FilePathCheckerModule.dll`을 가져와 SHA-256을 검증합니다. GSG HWP는 이
제3자 패키지나 보안 DLL의 소유권을 주장하거나 별도로 재라이선스하지 않습니다.

## 한글과컴퓨터 제품 및 Automation

한/글, HWP, HWPX, 한컴 및 관련 제품명·상표·Automation API와 공식 문서의
권리는 해당 권리자에게 있습니다. 한글과컴퓨터의 공식 안내에 따르면 한글
Automation은 개인의 비상업적 목적에는 자유롭게 이용할 수 있지만, 상업적으로
판매되는 솔루션이나 응용프로그램에 이용하려면 한글과컴퓨터의 승인과 별도
라이선스가 필요합니다.

- 한컴 Automation 안내: <https://developer.hancom.com/hwpautomation>

GSG HWP의 MIT License는 한컴 제품, Automation 또는 제3자 구성요소에 대한
상업적 이용 권한을 부여하지 않습니다. 이 프로젝트는 한글과컴퓨터의 공식
제품이 아니며 한글과컴퓨터의 보증이나 승인을 받았음을 의미하지 않습니다.

## 데모 영상과 이미지

`docs/media/`의 데모 영상과 썸네일은 `Copyright (c) 2026 inodesign. All
rights reserved.`입니다. 저장소 소개 페이지에서 열람할 수 있지만, 별도 허가
없이 복제·수정·재배포할 수 있는 권리는 MIT License에 포함되지 않습니다.
