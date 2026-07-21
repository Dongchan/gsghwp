# HancomEventBridge

`HancomEventBridge.exe` is a registration-free Win32 sidecar for the public
Hancom automation event interfaces. It attaches to one exact ROT display name,
subscribes to the HWP object's connection point, and writes newline-delimited
UTF-8 JSON events to stdout.

It is deliberately not an HWP add-in. It does not write the registry, does not
export `DllRegisterServer`, and does not require administrator rights. The MCP
launcher starts the Python server with the desktop user's medium-integrity
token; this child process inherits that token and can therefore see the same
ROT entries as the already-open HWP process.

Build with Visual Studio Build Tools:

```text
MSBuild.exe HancomEventBridge.vcxproj /p:Configuration=Release /p:Platform=Win32
```

Smoke-test the event encoder with `HancomEventBridge.exe --self-test`. Production
use passes `--moniker` and writes `shutdown` plus a newline to stdin before exit.
