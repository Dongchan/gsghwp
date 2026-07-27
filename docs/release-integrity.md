# 배포본 검증·코드 서명·백신 대응

이 문서는 지금 할 수 있는 검증과, 아직 못 하는 것을 나눠서 적습니다.
해시가 일치하면 **내려받은 파일이 게시된 것과 같다**는 뜻입니다. 코드 서명처럼 **누가 만들었는지**를
증명하지는 못합니다. 그 차이를 알고 쓰시기 바랍니다.

## v1.2.2 바이너리 SHA-256

| 파일 | SHA-256 |
|---|---|
| `gsg-hwp-plugin-v1.2.2.zip` | `7e4c19e73a0ab5826887e74a40888279af8ae2601a7c5edb82d27d06cf5ef2de` |
| `addon\HancomMcpLauncher\bin\Release\HancomMcpLauncher.exe` | `69c5109c2e4027c4e2ecb915926a00c64c1ad63e16673267f2232e29cae70fbb` |
| `addon\HancomEventBridge\bin\Release\HancomEventBridge.exe` | `50611c5e6a5607b830dc5274bbd0f1fb4485bcf66590efaf4f770eb82eeb9f0d` |
| `addon\HancomLiveBridgeNative\bin\0.5.124\HancomLiveBridge.dll` | `6e1e3f06131c8f636e7b7aa770e3b0a7274ec2c2b8767c902dd98ab23f04e379` |

실행 파일 3개(런처·이벤트 브리지·네이티브 DLL)는 v1.2.0과 동일합니다. v1.2.2는 파이썬
소스만 바뀌었으므로 세 값이 그대로입니다.

런처와 네이티브 DLL의 값은 배포본 `compatibility-manifest.json`의
`launcher_sha256`, `native_sha256`와 같습니다. 릴리스 자산 `latest.json`의 `package_sha256`는
위 ZIP 값과 같습니다.

## 직접 확인하는 방법

### 1. 내려받은 ZIP

```powershell
Get-FileHash -Algorithm SHA256 .\gsg-hwp-plugin-v1.2.2.zip
```

### 2. 압축을 푼 뒤 실행 파일 3개

```powershell
$root = "압축을 푼 경로\gsg-hwp"
@(
  "$root\addon\HancomMcpLauncher\bin\Release\HancomMcpLauncher.exe",
  "$root\addon\HancomEventBridge\bin\Release\HancomEventBridge.exe",
  "$root\addon\HancomLiveBridgeNative\bin\0.5.124\HancomLiveBridge.dll"
) | ForEach-Object { Get-FileHash -Algorithm SHA256 $_ }
```

### 3. 매니페스트 기록값과 대조

```powershell
$m = Get-Content "$root\compatibility-manifest.json" -Raw | ConvertFrom-Json
$m.launcher_sha256
$m.native_sha256
```

### 4. 설치된 상태에서 다시 확인

설치 후 네이티브 DLL은 다음 경로로 복사됩니다.

```powershell
Get-FileHash -Algorithm SHA256 `
  "$env:LOCALAPPDATA\HancomDocumentAutomation\native\0.5.124\HancomLiveBridge.dll"
```

MCP는 시작할 때 런처와 네이티브 DLL을 매니페스트 값과 스스로 대조하며,
일치하지 않으면 서버를 시작하지 않습니다.

## 코드 서명

**현재 배포본에는 코드 서명이 없습니다.**

Authenticode 인증서는 발급 비용이 매년 발생합니다. 이 플러그인은 무상으로 공개하는 재능기부 성격이라
현재는 인증서를 구매하지 않았습니다. 대신 위 해시 공개와 시작 시 자체 검증으로 무결성만 확인합니다.

서명이 없으면 다음을 확인할 수 없습니다.

- 게시자가 누구인지
- 배포 경로 중간에서 바뀌었는지 (해시를 **게시된 값과 직접 대조**하면 이 부분은 보완됩니다)

기업 환경에서 서명이 필수라면 이슈로 알려주십시오. 수요가 확인되면 인증서 도입을 검토하겠습니다.

## 백신 오탐 대응

이 플러그인은 한/글 프로세스 안에서 동작하는 DLL을 등록합니다. 동작 자체가 정상이지만,
행위 기반 탐지를 쓰는 백신이 이를 의심스럽게 볼 수 있습니다.

실측상 검사 후 자동으로 풀리는 경우가 많지만, 기업용 백신은 즉시 격리해 **설치가 실패**할 수 있습니다.

### 설치 전에 예외 등록

Windows Defender:

```powershell
Add-MpPreference -ExclusionPath "$env:LOCALAPPDATA\HancomDocumentAutomation"
Add-MpPreference -ExclusionPath "$env:LOCALAPPDATA\GSG_HWP"
Add-MpPreference -ExclusionPath "플러그인을 설치할 경로"
```

기업용 백신(알약, Symantec 등)은 사용자가 예외를 등록하지 못하도록 잠겨 있는 경우가 많습니다.
그때는 위 해시 목록과 함께 보안 담당자에게 예외 등록을 요청하십시오.

### 이미 격리된 경우

Windows Defender에서 격리된 항목을 복원합니다.

```powershell
& "$env:ProgramFiles\Windows Defender\MpCmdRun.exe" -Restore -ListAll
& "$env:ProgramFiles\Windows Defender\MpCmdRun.exe" -Restore -Name "격리 목록에서 확인한 이름"
```

알약 등 기업용 백신은 제품의 격리 보관함에서 복원합니다.

**복원한 파일은 반드시 위 해시와 대조하십시오.** 격리·복원 과정에서 파일이 바뀌었다면 값이 달라집니다.
값이 다르면 복원본을 쓰지 말고 릴리스에서 다시 내려받으십시오.

### 복원이 안 되는 경우

격리된 파일을 되살릴 수 없으면 다시 설치하는 편이 확실합니다.

1. 백신 예외를 먼저 등록합니다.
2. 릴리스에서 ZIP을 다시 내려받고 해시를 확인합니다.
3. `install.ps1` 미리보기를 실행해 무엇이 바뀌는지 확인합니다.
4. `install.ps1 -AcceptChanges`로 설치합니다.

되돌리려면 `restore-update.ps1`(직전 업데이트만) 또는 `uninstall.ps1`(최초 설치 전 상태)을 사용합니다.

### 오탐 신고

오탐이 반복되면 백신 제조사에 신고해주시면 도움이 됩니다. 신고할 때 위 해시를 함께 적으면
분석이 빨라집니다.

## 설치가 바꾸는 것

설치기가 건드리는 범위는 다음으로 한정됩니다. 한/글 설치 폴더, `HKLM`, `regsvr32`,
관리자 권한은 사용하지 않습니다.

| 대상 | 경로 |
|---|---|
| 네이티브 DLL | `%LOCALAPPDATA%\HancomDocumentAutomation\native\0.5.124\HancomLiveBridge.dll` |
| 파일 경로 보안 DLL | `%LOCALAPPDATA%\GSG_HWP\security\FilePathCheckerModule.dll` |
| 레지스트리 | `HKCU\Software\HNC\HwpUserAction\Modules`, `HKCU\Software\HNC\HwpAutomation\Modules` |
| Python 환경 | `%LOCALAPPDATA%\GSG_HWP\runtime\1.2.2\.venv` |

`FilePathCheckerModule.dll`은 잠금된 `pyhwpx==1.6.6` 환경에 포함된 것을 쓰며,
설치기가 SHA-256을 확인한 뒤 복사합니다.
