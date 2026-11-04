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
3. **No Microsoft PDB symbols at runtime**: `Wh_FindFirstSymbol*` /
   `WindhawkUtils::HookSymbols` always fail in the sandbox (by design of the shim); mods
   depending on internal symbols are reported `unverifiable: internal-symbols` and
   require real Windows. Since Feature 1 (`docs/symbol-matrix.md`) the *names* can still
   be checked offline: `python3 -m awt.symbols mod.wh.cpp` reads the public PDBs of the
   builds in `configs/symbol-matrix.yaml` and reports per symbol per build
   `found`/`missing`/`ambiguous`/`unknown`. That is a statement about names only — no
   hook is installed, nothing is executed, and the verdict ladder is unchanged.
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

## Added by Feature 1 (symbol matrix), observed 2026-11-04

11. **`msdl.microsoft.com` is unreachable from some sandboxes** (TLS handshake blocked
    for `curl`/`urllib` here). The download path is implemented and unit-tested against a
    local HTTP server on `127.0.0.1` serving synthetic PE/PDB images; on a host without
    symbol-server access every cell is `unknown` with the reason, never `found`.
12. **No real Microsoft PDB has been parsed in this sandbox.** The MSF/DBI/GSI reader is
    exercised against synthetic containers built to the documented layout
    (`tests/unit/fixtures`), including the `S_GPROC32` layout ambiguity, which is resolved
    by probing both layouts and reporting the choice. `llvm-pdbutil` is absent here, so
    the optional `--cross-check` against a real reader has not run either.
13. **The demangler is partial.** 357 of the 375 distinct decorated names found in the
    860 published mods decode with confidence `structural`; 18 (Chromium-style
    `__Cr@std`, `_SU`, `_N2@Z` encodings) decode only partially and are then compared by
    qualified name alone, with a note. Template arguments are never compared.
    `llvm-undname`, when installed, upgrades those verdicts to `exact`.
14. **Extraction is static analysis.** Names built at runtime (string concatenation,
    tables filled in a loop) cannot be seen; 302 of the 302 published mods that declare a
    `SYMBOL_HOOK` table were covered, which is an observation about those mods, not a
    guarantee. When the target module cannot be determined the request is reported with
    `module_basis: unresolved` and its cells stay `unknown`.
15. **Build identity is per binary, not per machine.** A build id maps to one specific
    PE through winbindex or the pinned table; a machine with a different cumulative
    update can differ. The report records which binary was used (version, timestamp,
    size, PDB GUID) so the claim can be checked.

## Added by Feature 2 (execution backends), observed 2026-11-04

16. **No native Windows run happened in the development sandbox.** The
    `windows-runner` backend is implemented and unit-tested for its environment
    detection, refusal to run off Windows and `not_executed` reporting on Linux; its
    native execution path is exercised only by `.github/workflows/windows-ci.yml`. Read
    that check run for evidence, not this repository.
17. **GitHub-hosted Windows runners are Windows Server.** `windows-latest` is build
    20348 (Server 2022): no immersive taskbar, no Start-menu experience, a different
    explorer. Shell-related outcomes from CI are marked `indicative` in the report and
    are not evidence about a user's desktop. Build 26100 is deliberately reported as
    `shell_profile: unknown` when `ProductType` is unavailable, because it is both
    Windows 11 24H2 and Windows Server 2025.
18. **Running on real Windows does not make the shim the Windhawk engine.** The mod is
    still loaded by `host/host.cpp` through the bench's shim: public API hooks are real
    function hooks in a real Windows process, but `Wh_FindFirstSymbol`/`HookSymbols`
    resolution is still the shim's, so internal-symbol mods stay `not_verifiable_here`
    on every backend. There is no injection into `explorer.exe` or
    `twinui.pcshell.dll`, so a taskbar mod's *effect on the taskbar* is not observed
    anywhere.
19. **The self-hosted VM backend has never driven a real VM in this project.** It is
    tested end to end against `tests/unit/fixtures/fake_vm.py`, which plays the
    hypervisor and the in-guest agent, including never booting, heartbeating without
    finishing, a failed snapshot revert, a guest crash and a truncated screenshot. A
    user's QEMU/VirtualBox/Hyper-V configuration is theirs to validate; the report says
    what the guest reported, and `not_executed` with the expired timeout when the guest
    never answered.
20. **Screenshots are evidence only when they are real.** A capture that is a single
    colour (what a service/session-0 desktop produces) is flagged in the report rather
    than presented as a desktop; a file whose magic bytes do not match its extension is
    reported as unavailable. No screenshot of a Microsoft interface is committed to this
    repository.

## Added by Feature 3 (visual comparison), observed 2026-11-04

21. **Visual similarity is not a verdict, in either direction.** A `match` can be a mod
    that hooks nothing and leaves the desktop untouched; a `mismatch` can be a working
    mod that draws the right thing in a different font. The section is added next to
    `verdict.level` and never changes it, and `verdict.visual_comparison_note` says so in
    words.
22. **No reference screenshot has been compared yet.** `awt.refshots` (Feature 4) does
    not exist, so `.cache/refshots/` is empty and every configured reference in
    `configs/visual.example.yaml` currently reports `unavailable` with the missing path.
    The comparison code is exercised end to end on generated images in
    `tests/unit/fixtures/images.py`, never on a capture of a real Windows desktop.
23. **The two SSIM implementations are not interchangeable.** With numpy the standard
    11×11 Gaussian window runs; without it, non-overlapping 8×8 blocks with a uniform
    window. The same pair scores 0.986 and 0.886 in one of this repository's tests. Every
    result records its implementation and the pure path carries a note that its number is
    not comparable with scikit-image, Pillow or a paper; thresholds can be set per
    implementation (`ssim_min_numpy`, `ssim_min_pure`).
24. **Normalisation weakens the numbers, and is disclosed rather than avoided.**
    Resizing to match a resolution is a box average that blurs edges (one test drops
    SSIM to 0.848 purely from resampling); downscaling to keep the pure-Python path
    usable makes region coordinates approximate and averages away one-pixel differences.
    Both appear in `caveats` and in `normalisation.steps`. Colour management is **not**
    applied: pHYs, gAMA and iCCP are read and reported, and pixels are compared as
    stored in the file, so a wide-gamut capture and an sRGB reference are compared
    numerically rather than perceptually. 16-bit images are reduced to their high byte.
25. **Things this comparison cannot see.** No OCR, so a wrong glyph at the right size can
    pass; no font-rendering model, so ClearType, hinting and sub-pixel positioning must be
    absorbed by `tolerance` or excluded by `roi`; no notion of time, so a clock, a
    blinking caret or an animation makes a correct mod differ; no DPI virtualisation, so a
    150 %-scaled capture is simply a different image. The PNG reader refuses interlaced
    images outright (Adam7 is not implemented) instead of guessing.
