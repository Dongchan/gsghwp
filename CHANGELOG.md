# 변경 이력

배포 버전은 Semantic Versioning 형식인 `주버전.부버전.수정버전`을 사용합니다.

## [1.1.0] - 2026-07-25

### FULL 소스와 네이티브 런타임 교체

- `GSG_HWP_BETA_현재버전_FULL_0.5.73-dev.1_native-0.5.121_20260725-171145.tar.gz`의 SHA-256 `43916be6b363e7a5bcda02104d2ab24c02b1d2bae9704c36560cffc5fae26eed`를 기준 소스로 사용
- MCP 런타임 `0.3.89`, Win32 C++/ATL 네이티브 브리지 `0.5.121`, 프로토콜 `12`로 업데이트
- 작업 중심 공개 도구 44개와 런타임 재로드 1개, 총 45개 및 QA 도구 62개 반영
- 참고 이미지 분석, 편집 가능한 네이티브 레이아웃 생성·패치, 배치 사전 검사, 작업 상태 조회와 강화된 문서 세션 기능 포함
- 공식 API 제외 목록은 검증된 4개 `SaveHistoryItem`, `IHwpObject.ExportStyle`, `IHwpObject.ImportStyle`, `IDHwpParameterArray.Clone`을 그대로 유지

### 자동 업데이트와 격리 런타임

- GitHub `main`에 새 배포 버전이 반영되면 안전 QA 후 태그 Release, 플러그인 ZIP과 `latest.json`을 생성하는 워크플로 추가
- MCP 시작 시 6시간 간격으로 공식 GitHub Release를 확인하고, 태그 고정 URL·SHA-256·ZIP 경로·패키지 메타데이터·MCP import 검증을 통과한 버전만 활성화
- 한/글 실행 중에는 DLL·레지스트리 변경을 보류하고, 네트워크 또는 검증 실패 시 현재 버전을 계속 실행
- 최초 설치와 자동 업데이트 모두 `uv sync --managed-python`으로 `%LOCALAPPDATA%\GSG_HWP\runtime\<배포 버전>\.venv`를 만들고 해당 Python만 실행
- 업데이트 직전 DLL·HKCU 레지스트리·설치 상태·활성 패키지 상태를 별도 백업
- `restore-update.ps1`로 직전 자동 업데이트를 복원하고 `uninstall.ps1`로 최초 설치 전 상태를 복원하도록 분리

### 배포 정리와 안내

- 개발 `.venv`, 캐시·로그·중간 빌드 파일, 구버전 DLL, 개인 업무 스크립트·fixture·절대 경로를 제외
- 배포 버전 `1.1.0`, 원본 소스 `0.5.73-dev.1`, 개발자 `inodesign` 메타데이터와 README·설치 에이전트 지침을 일치시킴

## [1.0.4] - 2026-07-23

### 공식 API 라우팅 정정

- 공식 API 사례 번호를 분류별 순번이 아닌 전체 목록의 전역 순번으로 해석하던 오류 수정
- 실제 검증 실패 4개를 `action:0608:SaveHistoryItem`, `automation:0067:IHwpObject.ExportStyle`, `automation:0068:IHwpObject.ImportStyle`, `automation:0365:IDHwpParameterArray.Clone`으로 정정
- 정상 Action인 `CharShapeTextColorGreen`, `CharShapeTextColorRed`, `MakeIndex`를 다시 라우팅하고 Action 933개, Automation 373개, ParameterSet 142개로 활성 경로 재구성
- 런타임 정책, 호환성 매니페스트, 설치 QA, 회귀 테스트와 README의 제외 목록을 동일하게 맞춤
- MCP `0.3.87`, 네이티브 브리지 `0.5.55`, DLL·레지스트리 설치와 백업·원상복구 동작은 변경하지 않음

## [1.0.3] - 2026-07-22

### 라이선스와 권리 범위 명확화

- GSG HWP 자체 소스코드와 해당 소스에서 빌드한 실행 파일을 `Copyright (c) 2026 inodesign`의 MIT License로 공개
- `LICENSE`와 `THIRD_PARTY_NOTICES.md`를 추가하고 Python 직접 의존성의 고정 버전·라이선스·프로젝트 출처를 기록
- `pyhwpx.Hwp` 어댑터와 `pyhwpx==1.6.6`에서 가져오는 `FilePathCheckerModule.dll`을 제3자 구성요소로 명시
- 한컴 Automation의 개인 비상업적 사용과 상업적 이용 시 별도 승인·라이선스 필요 조건을 한컴 공식 안내에 연결
- `docs/media/` 원본 영상과 썸네일은 MIT 대상에서 제외하고 inodesign의 권리 유보 자료로 구분
- MCP `0.3.87`, 네이티브 브리지 `0.5.55`, 공개 도구 38개와 설치 전 백업·원상복구 동작은 변경하지 않음

## [1.0.2] - 2026-07-22

### 2차 배포 기능 반영

- MCP 런타임을 `0.3.87`, Win32 C++/ATL 네이티브 브리지를 `0.5.55`로 업데이트
- 작업 중심 공개 도구 37개와 런타임 재로드 도구 1개, 총 38개로 확대
- `hwp_insert_layout`으로 현재 커서 또는 지정 쪽 뒤에 편집 가능한 네이티브 레이아웃 삽입 지원
- `hwp_list_window_states`로 열린 한/글 창의 문서·활성·가시 상태 조회 지원
- MCP 지침과 네이티브 레이아웃 참고문서를 읽을 수 있는 guidance resource 추가

### 불규칙 표와 편집 안정성

- 병합·분할 셀을 `CellTopology`로 해석해 실제 셀 주소와 논리 격자를 안정적으로 연결
- 표 병합·분할·서식 작업에 사전/사후 구조 검사, 외곽 크기 보호와 실패 시 rollback 보강
- 네이티브 스냅샷 호환, 선택 영역 대상 판별, 중간 문서 레이아웃과 참고 이미지 레이아웃 충실도 회귀 테스트 추가

### 배포 정리와 검증

- 제공된 `GSG_HWP_BETA(2차).tar.gz`의 SHA-256 `d6000730c75bf1df0c54b9cbc5d9a8e7fe3fd056679fc3fb748ae76fcc782c52`를 기준 소스로 사용
- 개발 캐시·로그·개인용 일회성 스크립트·구형 브리지·중간 빌드 산출물을 배포에서 제외
- 생산 EXE/DLL 3개를 디버그 정보 없이 다시 빌드해 개발자 로컬 경로를 제거
- 2차 아카이브의 테스트용 Automation 가짜 객체에서 빠진 `DeleteCtrl` 계약을 복원해 네이티브 통합 스모크 회귀 수정
- v1.0.1의 `FilePathCheckerModule.dll`, 사용자별 HKCU 백업과 `uninstall.ps1` 원상복구 정책 유지

## [1.0.1] - 2026-07-21

### 깨끗한 PC 설치 수정

- 잠금된 `pyhwpx==1.6.6` 환경의 `FilePathCheckerModule.dll`을 사용자 전용 경로로 설치
- DLL의 SHA-256을 설치 전에 검증하고 `HKCU\Software\HNC\HwpAutomation\Modules`에 `FilePathCheckerModule`로 등록
- 보안 모듈이 사전 등록되지 않은 PC에서 발생하던 `한컴 파일 경로 보안 모듈 등록이 거부되었습니다` 연결 오류 수정
- 한컴 설치 폴더, HKLM, `regsvr32`와 관리자 권한을 사용하지 않는 기존 정책 유지

### 백업과 원상복구

- 보안 DLL과 레지스트리 값의 기존 존재 여부·종류·값·원본 파일을 설치 전에 추가 백업
- 제거 시 기존 보안 상태를 정확히 복원하고, 원래 없던 항목은 GSG HWP가 추가한 것만 제거
- v1.0.0 활성 설치를 업데이트할 때 기존 네이티브 브리지 기준 백업은 유지하고 보안 모듈 기준 상태만 안전하게 추가
- 깨끗한 PC 설치, 기존 상태 왕복 복원, v1.0.0 백업 마이그레이션 격리 테스트 추가

## [1.0.0] - 2026-07-21

첫 공개 배포입니다. 개발자 메타데이터는 `inodesign`입니다.

### 런타임과 구조

- MCP 런타임 `0.3.85`
- Win32 C++/ATL UserAction 네이티브 브리지 `0.5.51`
- 네이티브 배치 프로토콜 `9`
- 작업 중심 공개 도구 35개와 런타임 재로드 도구 1개, 총 36개
- 공식 API 카탈로그 1,452개와 네이티브 활성 라우트 1,448개
- Codex 저장소 마켓플레이스, Claude Code 프로젝트 `.mcp.json`, 범용 stdio 시작 스크립트

### 라우팅 제외 API (v1.0.4 정정 반영)

- `action:0608:SaveHistoryItem`
- `automation:0067:IHwpObject.ExportStyle`
- `automation:0068:IHwpObject.ImportStyle`
- `automation:0365:IDHwpParameterArray.Clone`

### 설치 안전성

- DLL 또는 레지스트리를 바꾸기 전에 사용자별 원본 상태를 JSON과 원본 DLL로 백업
- 기존 레지스트리 키·값의 존재 여부, 값 종류와 값을 그대로 보존
- `uninstall.ps1`로 설치 전 DLL 및 레지스트리 상태 복원
- 설치·제거 모두 옵션 없는 실행은 미리보기만 수행
- HKLM, `regsvr32`, 관리자 권한을 사용하지 않음
- MCP 시작 시 DLL 자동 복사·레지스트리 자동 등록을 제거하고 설치 상태만 읽기 전용 검증

### 빠른 조회와 편집

- `Snapshot`, `InspectPageSummary`, `InspectPageV3`, `InspectPagesV3`, `InspectStructure` 기반 네이티브 조회
- 같은 문서 revision의 빠른 조회를 최대 32개 캐시하고 편집·외부 변경 시 무효화
- 빠른 조회의 `instance_id`를 표·그림·캡션·삭제 작업 대상으로 직접 재사용
- 문서 ID·경로·커서·선택·상태 토큰 확인 후 C++ `ExecuteActions` 실행
- 구조 조회와 `IHwpObject.CreatePageImage` 렌더를 조합한 결과 검수

### 배포 정리

- 개발 가상환경, 캐시, 로그, PDB, OBJ, LIB, EXP와 중간 빌드 산출물 제외
- 개인 업무 문서·경로와 일회성 개인 업무 스크립트 제외
- 사용하지 않는 C# 구형 브리지, API 하네스 바이너리와 구버전 네이티브 DLL 제외
- 생산 EXE/DLL을 디버그 정보 없이 다시 빌드해 로컬 PDB 경로 제거
- 플러그인 상대 경로와 `%LOCALAPPDATA%` 기반 사용자별 런타임 사용
- 런처, 이벤트 브리지, 네이티브 DLL의 SHA-256을 설치 전에 검증
