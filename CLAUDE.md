# GSG HWP — Claude Code 설치 지침

설치 전 `README.md`와 `AGENTS.md`를 읽고 그 안전 절차를 그대로 따른다.

- 실제 변경 전에 `install.ps1` 미리보기를 실행하고 DLL·HKCU 레지스트리 2개 값·백업·복원 방법을 사용자에게 안내한다.
- 한/글이 실행 중일 때 DLL을 교체하지 않는다.
- 실제 설치는 `install.ps1 -AcceptChanges`만 사용한다.
- 레지스트리 직접 편집, `regsvr32`, 관리자 권한, 키보드·마우스 자동화를 사용하지 않는다.
- 설치 후 `plugins/gsg-hwp/scripts/start-mcp.ps1`의 절대 경로를 `claude mcp add --transport stdio --scope user`로 등록한다.
- 제거 요청 시 한/글을 닫고 `uninstall.ps1` 미리보기 후 `uninstall.ps1 -AcceptChanges`로 설치 전 DLL·레지스트리 상태를 복원한다.
- 기능이 공개 도구에 없으면 1,448개 활성 공식 API 라우트를 조사해 필요한 기능만 전용 도구/recipe로 연결한다. 전체 API를 한꺼번에 도구로 노출하지 않는다.
