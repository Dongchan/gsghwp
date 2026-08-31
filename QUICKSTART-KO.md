# GSG HWP 빠른 설치

이 폴더는 Windows 10/11과 한컴오피스 한/글 2024에서 사용하는 로컬 MCP
배포본입니다. 설치는 현재 Windows 사용자 영역만 변경하며 관리자 권한,
HKLM, `regsvr32`를 사용하지 않습니다.

## 준비

1. 한/글과 MCP 클라이언트를 모두 종료합니다.
2. `uv`가 없다면 PowerShell에서 다음 명령으로 설치합니다.

   ```powershell
   winget install --id astral-sh.uv -e
   ```

3. `install.ps1`과 `.agents/`가 있는 저장소 루트 형상의 배포본 폴더를
   영구적으로 둘 위치에 준비합니다. 설치 후 이 폴더를 이동하거나 삭제하지
   마세요.

   ```powershell
   git clone https://github.com/innae1121-bit/gsghwp.git
   ```

   GitHub Release에 올라오는 `gsg-hwp-plugin-v<배포버전>.zip`은 자동
   업데이트가 쓰는 플러그인 전용 패키지여서 `install.ps1`이 들어 있지
   않습니다. 이 안내의 설치 절차에는 사용할 수 없습니다.

## 설치

준비한 배포본 폴더에서 PowerShell을 열고 먼저 변경사항을 확인합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

출력된 DLL, HKCU 레지스트리, 백업 경로를 확인한 다음 실제 설치를 실행합니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -AcceptChanges
```

설치기는 잠금된 Python 3.12 런타임을
`%LOCALAPPDATA%\GSG_HWP\runtime\<배포버전>\.venv`에 만들고, 네이티브 브리지와
한컴 파일 경로 보안 모듈을 사용자별 경로에 등록합니다. `<배포버전>` 자리의
실제 폴더 이름은 `plugins\gsg-hwp\compatibility-manifest.json`의
`distribution`에서 읽습니다.

## Codex 등록

배포본 폴더의 절대 경로를 사용합니다.

```powershell
codex plugin marketplace add "C:\경로\gsghwp"
codex plugin add gsg-hwp@gsg-hwp
codex plugin list
```

## Claude Code 등록

아래 `<배포경로>`를 배포본 폴더의 절대 경로로 바꿉니다.

```powershell
claude mcp add --transport stdio --scope user gsg-hwp-beta-live -- powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "<배포경로>\plugins\gsg-hwp\scripts\start-mcp.ps1"
claude mcp list
```

다른 stdio MCP 클라이언트도 같은 `powershell.exe` 명령과 인수를 등록하면
됩니다.

## 첫 실행 확인

1. 한/글을 새로 시작하고 HWP 문서를 하나 엽니다.
2. MCP 클라이언트를 새로 시작합니다.
3. `hwp_list_open_documents`를 실행해 열린 문서가 나오는지 확인합니다.
4. `hwp_inspect_page_fast`로 1쪽을 조회합니다.
5. 실제 문서를 수정하기 전까지는 저장 도구를 실행하지 않습니다.

## 복구와 제거

직전 업데이트로 되돌리기:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\restore-update.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\restore-update.ps1 -AcceptChanges
```

최초 설치 전 상태로 되돌리기:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1 -AcceptChanges
```

`uninstall.ps1 -AcceptChanges`는 DLL·레지스트리를 되돌리면서
`%LOCALAPPDATA%\GSG_HWP`의 `packages`와 `updater`도 지웁니다. `-KeepRuntime`을
주지 않으면 `runtime` 아래 설치된 모든 버전의 `.venv`까지 지웁니다. 설치 전
DLL과 레지스트리 백업은 `%LOCALAPPDATA%\GSG_HWP\backups`에 보존됩니다.

앱 등록도 함께 제거합니다.

```powershell
codex plugin remove gsg-hwp@gsg-hwp
codex plugin marketplace remove gsg-hwp
claude mcp remove gsg-hwp-beta-live
```

## 배포본 확인

릴리스에 함께 올라온 `latest.json`의 `package_sha256` 값과 내려받은 ZIP의
SHA-256이 일치해야 합니다.

```powershell
Get-FileHash -Algorithm SHA256 .\gsg-hwp-plugin-v<배포버전>.zip
```

`<배포버전>`은 `plugins\gsg-hwp\compatibility-manifest.json`의 `distribution`
값입니다. 자동 업데이트는 이 검증을 스스로 수행하므로 수동 확인은 선택입니다.
