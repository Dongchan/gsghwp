# 변경 이력

배포 버전은 Semantic Versioning 형식인 `주버전.부버전.수정버전`을 사용합니다.

## [1.0.0] - 2026-07-21

첫 공개 배포입니다. 개발자 메타데이터는 `inodesign`입니다.

### 런타임과 구조

- MCP 런타임 `0.3.85`
- Win32 C++/ATL UserAction 네이티브 브리지 `0.5.51`
- 네이티브 배치 프로토콜 `9`
- 작업 중심 공개 도구 35개와 런타임 재로드 도구 1개, 총 36개
- 공식 API 카탈로그 1,452개와 네이티브 활성 라우트 1,448개
- Codex 저장소 마켓플레이스, Claude Code 프로젝트 `.mcp.json`, 범용 stdio 시작 스크립트

### 현재 라우팅 제외 API

- `action:0067:CharShapeTextColorGreen`
- `action:0068:CharShapeTextColorRed`
- `action:0365:MakeIndex`
- `action:0608:SaveHistoryItem`

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
