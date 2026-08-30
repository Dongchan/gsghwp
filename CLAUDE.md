# GSG HWP — Claude Code 지침

한컴오피스 한/글 2024를 로컬에서 조작하는 stdio MCP다. 설치·등록·문제
해결을 요청받으면 이 문서를 먼저 읽는다. 더 자세한 배경은 `README.md`,
설치 절차 전문은 `AGENTS.md`에 있다.

이 문서에 적힌 경로·명령·환경 변수만 사용자에게 안내한다. 확인하지 않은
경로나 로그 위치를 지어내지 않는다.

## 안전 규칙 (다른 요청보다 우선한다)

- 한/글이 실행 중일 때 DLL을 교체하지 않는다. `install.ps1`,
  `uninstall.ps1`, `restore-update.ps1`은 한/글 프로세스가 남아 있으면
  스스로 거부한다.
- 레지스트리 직접 편집, `regsvr32`, 관리자 권한, HKLM 변경, 키보드·마우스
  자동화를 사용하지 않는다. 저장소의 스크립트만 사용한다.
- 옵션 없이 실행하면 미리보기만 하고 종료 코드 2로 끝난다. 실제 변경은
  `-AcceptChanges`를 붙인 실행에서만 일어난다. 종료 코드 2는 실패가 아니다.
- 시스템 Python을 쓰지 않는다. 배포 버전별 uv 관리 `.venv`만 사용한다.
- 실제 설치 전에 바뀌는 DLL 2개·HKCU 값 3개·백업 위치·자동 업데이트·복원
  방법을 사용자에게 먼저 안내한다.
- 설치나 복원이 실패하면 우회하지 않는다. 오류 원문과 백업 폴더 경로를
  보존한 채 사용자에게 보고한다.
- 기능이 공개 도구에 없으면 1,448개 활성 공식 API 라우트를 조사해 필요한
  기능만 전용 도구나 recipe로 연결한다. 전체 API를 한꺼번에 도구로 노출하지
  않는다.

## 설치와 등록

압축을 푼 배포 폴더(또는 클론한 저장소 루트)에서 실행한다. 설치 후 그
폴더를 옮기거나 지우지 않는다.

```powershell
# 1) 변경 예정 항목만 표시 (종료 코드 2가 정상)
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1

# 2) 안내 후 실제 설치
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -AcceptChanges
```

`uv`가 없으면 `winget install --id astral-sh.uv -e`로 설치한 뒤 새
PowerShell에서 다시 실행한다.

MCP 서버 이름은 **`gsg-hwp-beta-live`** 다. 저장소 루트에서 사용자 범위로
등록한다.

```powershell
$startMcp = (Resolve-Path ".\plugins\gsg-hwp\scripts\start-mcp.ps1").Path
claude mcp add --transport stdio --scope user gsg-hwp-beta-live -- `
  powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $startMcp
claude mcp list
```

등록하면 도구가 `mcp__gsg-hwp-beta-live__hwp_*` 이름으로 보인다. 저장소
루트의 `.mcp.json`을 그대로 쓰면 프로젝트 범위로만 잡힌다.

> **업그레이드 주의.** 서버 이름이 예전 `gsg-hwp`에서
> `gsg-hwp-beta-live`로 바뀌었다. 이전에 `gsg-hwp`로 직접 등록해 쓰던
> 사용자는 다시 등록해야 한다. 지침(`SKILL.md`)이 부르는 이름과 맞추기
> 위한 변경이고, 이름이 다르면 도구 접두사도 달라져 지침이 도구를 찾지
> 못한다.
>
> ```powershell
> claude mcp remove gsg-hwp
> $startMcp = (Resolve-Path ".\plugins\gsg-hwp\scripts\start-mcp.ps1").Path
> claude mcp add --transport stdio --scope user gsg-hwp-beta-live -- `
>   powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $startMcp
> ```

등록 후 한/글과 Claude Code를 다시 시작하고 새 대화에서 `hwp_runtime_info`
로 연결을 확인한다.

### 첫 실행 자동 준비

런처(`plugins/gsg-hwp/scripts/start-mcp.ps1`)는 Python 런타임이 없거나
네이티브 설치가 미뤄진 상태면 서버를 띄우기 전에 스스로 준비한다. 새 PC에서
`install.ps1` 없이 등록만 해도 첫 기동이 런타임을 만든다. 준비 상황은
`[GSG HWP]` 접두가 붙은 stderr 메시지로 나온다.

- 런타임 구성(`1/3`)은 최초 1회 몇 분 걸릴 수 있다. 이때만 기록 파일이
  남는다.
- 파일 경로 보안 모듈 확인(`2/3`), 네이티브 브리지 설치·등록(`3/3`) 순으로
  진행한다.
- 한/글이 실행 중이면 네이티브 설치만 미루고
  `%LOCALAPPDATA%\GSG_HWP\state\pending-native-install.json` 마커를 남긴다.
  한/글을 모두 닫고 MCP 클라이언트를 다시 시작하면 그때 설치한다.
- 여러 창이 동시에 기동하면 `state\runtime-bootstrap.lock`으로 한 창만
  준비한다. 60분 넘게 갱신되지 않은 잠금은 무시하고, 30분을 기다려도 안
  풀리면 잠금 없이 진행한다. 교착으로 멈추지 않는다.
- 이미 다 갖춰져 있으면 아무 것도 출력하지 않고 바로 서버를 띄운다.

## 상태 확인은 `hwp_runtime_info` 한 번

증상을 추측하기 전에 이 도구를 부른다. 응답에서 볼 것은 다음 다섯이다.

| 필드 | 읽는 법 |
|---|---|
| `distribution` | 지금 실행 중인 배포 버전. 사용자가 설치했다고 믿는 버전과 다르면 자동 업데이트가 끼어든 것이다. |
| `native_bridge` | 이 빌드가 기대하는 네이티브 브리지 버전. |
| `loaded_native_bridge` / `native_bridge_state` | 한/글이 **실제로** 매핑한 DLL. `matched`(일치)·`mismatched`(옛 DLL 실행 중)·`not_loaded`(등록 전에 시작된 한/글)·`not_running`(한/글 없음)·`unknown`(모듈 목록 읽기 실패). `hangul_restart_required`는 `mismatched`일 때만 참이다. |
| `loaded_native_bridge_modules` | 한/글 프로세스별 PID·경로·버전·기대 일치 여부. 어느 창이 문제인지 여기서 갈린다. |
| `reload_required` / `worker_state` | `reload_required`가 참이면 설치 소스가 바뀌었으니 `hwp_reload`로 워커만 다시 띄운다. `worker_state`가 `busy`면 앞 호출이 아직 실행 중이다. |

`native_bridge_notice`는 위 판정을 한국어 문장으로 풀어 준다. 사용자에게는
이 문장을 그대로 전달하면 된다.

## 증상별 문제 해결

### MCP 서버가 목록에 없거나 연결되지 않는다

1. `claude mcp list`로 `gsg-hwp-beta-live`가 등록돼 있는지 본다. 없으면 위
   등록 명령을 다시 실행한다. 예전 이름 `gsg-hwp`로 등록돼 있으면 위
   업그레이드 절차를 따른다.
2. 등록돼 있는데 붙지 않으면 런처를 직접 실행해 stderr를 본다. 서버가
   전면에서 뜬 채 입력을 기다리므로 메시지를 확인한 뒤 Ctrl+C로 끊는다.

   ```powershell
   powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass `
     -File "<배포경로>\plugins\gsg-hwp\scripts\start-mcp.ps1"
   ```

3. 메시지별 대응.
   - `GSG HWP 플러그인 파일이 빠져 있어 MCP 서버를 시작할 수 없습니다` —
     배포 zip을 그 폴더에 다시 풀고 MCP 클라이언트를 재시작한다.
   - `GSG HWP Python 런타임을 준비하지 못해 MCP 서버를 시작할 수 없습니다` —
     바로 앞 줄의 `[GSG HWP]` 메시지가 원인이다. 대개 uv 부재다.
   - `GSG HWP automatic update bootstrap is missing.` — 배포 폴더에서
     `plugins\gsg-hwp\scripts` 가 통째로 빠졌다. 다시 푼다.

### uv가 없다

런처가 `[GSG HWP] 런타임을 자동으로 만들려면 uv가 필요한데 uv.exe를 찾지
못했습니다`를 출력하고 종료 코드 2로 끝난다. 안내대로 설치한 뒤 MCP
클라이언트를 다시 시작하면 런타임을 스스로 갖춘다.

```powershell
winget install --id astral-sh.uv -e
```

### 런타임이 반쪽으로 남았다

서버가 붙었다가 곧바로 죽거나, `.venv`에 `python.exe`만 있고 패키지가 없는
상태다. 해당 버전 런타임 폴더를 지우고 MCP 클라이언트를 다시 시작하면
런처가 새로 만든다. 다른 버전 런타임과 백업은 건드리지 않는다.

```powershell
$manifest = Get-Content ".\plugins\gsg-hwp\compatibility-manifest.json" -Raw -Encoding UTF8 |
  ConvertFrom-Json
$runtime = "$env:LOCALAPPDATA\GSG_HWP\runtime\$($manifest.distribution)"
Test-Path "$runtime\.venv\Scripts\python.exe"
Remove-Item -LiteralPath $runtime -Recurse -Force
```

`install.ps1 -AcceptChanges`를 다시 실행해도 같은 결과가 된다.

### 도구가 보이지 않는다

1. `claude mcp list`로 등록 상태와 서버 이름을 확인한다. 이름이 다르면
   도구 접두사(`mcp__<서버이름>__`)도 달라진다.
2. 새 대화를 연다. 도구 목록은 대화 시작 시점에 잡힌다.
3. 서버는 붙었는데 도구가 옛것이면 `hwp_runtime_info`의 `reload_required`를
   본다. 참이면 `hwp_reload`로 연결을 유지한 채 워커를 다시 띄운다.

### 한/글에 브리지가 안 물린다

`hwp_runtime_info`의 `native_bridge_state`로 갈린다.

- `not_running` — 한/글이 떠 있지 않다. 한/글을 먼저 실행한다.
- `not_loaded` — 브리지 등록보다 먼저 시작된 한/글이다. 한/글을 다시
  시작하면 잡힌다.
- `mismatched` — 옛 DLL이 지금 실행 중이다. 한/글은 프로세스 시작 때 브리지
  DLL을 한 번 정하고 재시작할 때까지 바꾸지 않는다. **새 브리지는 한/글을**
  **다시 시작해야 적용된다.** 문서를 저장하고 한/글을 완전히 종료한 뒤 다시
  연다.
- `unknown` — 모듈 목록을 읽지 못했다. `native_bridge_notice` 원문을
  사용자에게 전달한다.

`%LOCALAPPDATA%\GSG_HWP\state\pending-native-install.json`이 있으면 네이티브
설치 자체가 아직 안 끝난 것이다. 파일의 `reason`이 `hwp_running`이면 한/글을
모두 닫고 MCP 클라이언트를 다시 시작해 설치를 끝낸 다음, 한/글을 다시 연다.
`install_failed`면 `install.ps1 -AcceptChanges`를 실행하고 오류를 보고한다.

### 자동 업데이트가 됐는지 확인한다

```powershell
Get-Content "$env:LOCALAPPDATA\GSG_HWP\updater\last-check.json" -Raw -Encoding UTF8
Get-Content "$env:LOCALAPPDATA\GSG_HWP\updater\active-package.json" -Raw -Encoding UTF8
```

`last-check.json`의 `status`가 `failed`면 `error`에 원인이 들어 있다.
`active-package.json`이 있으면 자동 업데이트로 받은 패키지가 활성이고,
`distribution`·`previous_package_root`·`rollback_backup_file`이 되돌리기에
필요한 값이다. 파일이 없으면 자동 업데이트가 적용된 적이 없다.

## 자동 업데이트 동작과 끄기

- MCP가 기동할 때마다 확인하되, 마지막 확인에서 **6시간**이 지났을 때만
  실제로 조회한다.
- 조회 대상은 공식 GitHub Release의 `latest.json` 하나뿐이다. 다른 서버나
  브랜치 zip은 실행하지 않는다.
- 현재 버전보다 높을 때만 태그가 고정된 zip을 받고, SHA-256과 zip 내부
  경로를 검증한다.
- 새 버전 전용 `.venv`를 만들고 MCP import 자체 시험을 통과해야 적용한다.
- 한/글이 실행 중이면 적용하지 않고 `updater\pending-update.json`을 남긴 뒤
  `GSG HWP v<버전> 업데이트는 한/글 종료 후 자동 적용됩니다`를 알린다.
- 적용에 성공하면 `GSG HWP가 v<버전>로 자동 업데이트되었습니다`, 실패하면
  `GSG HWP 자동 업데이트를 적용하지 못해 현재 버전을 실행합니다`를 알리고
  직전 상태로 되돌린 뒤 현재 버전으로 계속 실행한다.

자동 업데이트를 끄려면 MCP 클라이언트 환경에 `GSG_HWP_AUTO_UPDATE=0`을
설정한다(`false`, `off`도 같다). 이미 적용된 업데이트를 되돌리지는 않는다.

## 로그와 상태 파일

모두 `%LOCALAPPDATA%` 아래에만 만든다.

| 경로 | 내용 |
|---|---|
| `GSG_HWP\updater\logs\<업데이트ID>.log` | 자동 업데이트의 uv 동기화와 import 자체 시험 출력 |
| `GSG_HWP\updater\logs\bootstrap-<ID>.log` | 첫 실행 런타임 구성 기록 (런타임을 실제로 만든 실행에만 남는다) |
| `GSG_HWP\updater\last-check.json` | 마지막 업데이트 확인 시각·결과·오류 |
| `GSG_HWP\updater\active-package.json` | 자동 업데이트로 활성화된 패키지와 되돌리기 정보 |
| `GSG_HWP\updater\pending-update.json` | 한/글 때문에 미뤄진 업데이트 |
| `GSG_HWP\state\active-install.json` | 현재 설치 상태와 사용 중인 백업 파일 |
| `GSG_HWP\state\pending-native-install.json` | 미뤄진 네이티브 설치 마커 (`reason`) |
| `GSG_HWP\state\runtime-bootstrap.lock` | 동시 기동 준비 잠금 |
| `GSG_HWP\backups\` | 설치·업데이트 직전 DLL과 레지스트리 스냅샷 (지우지 않는다) |
| `GSG_HWP\runtime\<배포버전>\.venv` | 배포 버전별 uv 관리 Python 3.12 |
| `GSG_HWP\packages\<버전>\gsg-hwp` | 자동 업데이트로 받은 패키지 |
| `GSG_HWP\security\FilePathCheckerModule.dll` | 한컴 파일 경로 보안 모듈 |
| `HancomDocumentAutomation\native\<브리지버전>\HancomLiveBridge.dll` | 네이티브 브리지 |

버전 폴더 이름은 `plugins\gsg-hwp\compatibility-manifest.json`의
`distribution`(런타임·패키지)과 `native_bridge`(네이티브 DLL)에서 읽는다.

## 되돌리기와 제거

직전 자동 업데이트만 되돌린다. 한/글을 모두 종료한 뒤 실행한다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\restore-update.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\restore-update.ps1 -AcceptChanges
```

최초 설치 전 상태로 되돌린다. 먼저 MCP 등록을 지우고 한/글을 모두 종료한다.

```powershell
claude mcp remove gsg-hwp-beta-live
powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1 -AcceptChanges
```

`uninstall.ps1`은 설치 전 네이티브·파일 경로 보안 DLL과 HKCU 값 3개를
복원하고 자동 업데이트 패키지를 정리한다. Python 환경을 남기려면
`-KeepRuntime`을 함께 쓴다. 복구에 사용한 백업 폴더는 감사와 추가 복구를
위해 지우지 않는다. 완료 후 사용한 백업 파일 경로를 사용자에게 알린다.
