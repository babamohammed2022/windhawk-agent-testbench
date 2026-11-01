# Limitations found in this environment (all observed, 2026-11-01, Wine 10.0 Debian headless)

1. **Remote `CreateRemoteThread` + `LoadLibraryW` injection is broken under Wine 10.0.**
   The image mapped by a remote `LoadLibraryW` kept its first `.text` page
   non-executable: executing the mod entry point faulted with
   `Unhandled page fault on execute access to <base>+0x13fd`. Reproduced twice,
   with and without a delayed init thread. Abandoned in favour of documented
   alternatives.
2. **`AppInit_DLLs` is not honoured by this Wine build** (registry values set under
   `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Windows`, verified written;
   target process never loaded the DLL). Consequence: the loader falls back to the
   simulated in-process host, and every `include_analysis[*]` entry records
   `mode: "simulated"` with this reason.
3. **No Microsoft PDB symbols**: `Wh_FindFirstSymbol*` / `WindhawkUtils::HookSymbols`
   always fail in the sandbox (by design of the shim); mods depending on internal
   symbols are reported `unverifiable: internal-symbols` and require real Windows.
4. **Wine's explorer desktop mode** works and provides `Shell_TrayWnd` (probe sees it,
   hidden), but the mod cannot be loaded into explorer for the reasons above; tray
   behaviour is therefore exercised through a tray client activity in the simulated
   host (`Shell_NotifyIconW` NIM_ADD/MODIFY/DELETE) and, when requested, a real
   `trayapp.exe` running against Wine's tray.
5. **Fonts**: Segoe UI families do not exist; substitute metrics change text layout in
   screenshots. (The companion start-screen project installs renamed Open Sans faces
   as Segoe UI/Light/Semibold when visual fidelity matters.)
6. **Graphics**: no GPU; Direct2D runs through software GL (llvmpipe). Animations keep
   their durations (time-based) but render fewer intermediate frames.
7. **Crash handling**: `CrashHandler` is disabled in the prefix registry by setup.sh;
   otherwise winedbg attaches and hangs headless runs.
8. **32-bit mods**: only the x86-64 toolchain/Wine are installed; `@architecture`
   values other than x86-64 are reported as build failures.
9. **`Wh_GetUrlContent`, `Wh_Disasm`, dynamic-select options**: stubbed (logged and
   counted in `shim.not_implemented_called`); network fetches are out of scope.
10. **WTS/logon-time dependent mod logic** (e.g. open-at-logon guards) sees no valid
    logon time under Wine; such paths stay unexercised, which is correct defensive
    behaviour, not a bug.
