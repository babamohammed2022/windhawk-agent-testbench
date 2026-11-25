# Hybrid desktop lab (Linux + Wine + Xvfb)

A repeatable laboratory for **mods that create or change windows, menus and
flyouts**. It runs an original Win32 scene under Wine, loads a mod through this
bench's shim, applies a scripted interaction, captures the simulated desktop and
writes a structured report with concrete assertions.

It is **not** Windows Explorer, not a shell replacement, not the Windhawk engine
and not a claim that a mod works on a real Windows desktop. It reduces manual
experiments; it does not replace Windows calibration.

## Sources, code and licensing

* All new C++ and Python code in this feature was written from scratch for this
  repository. The observable Win32 behaviour used here comes from **public API
  documentation** (Microsoft Learn: [CreateWindowExW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-createwindowexw),
  [DrawTextW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-drawtextw),
  [BitBlt](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-bitblt),
  [SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput),
  [SHAppBarMessage](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shappbarmessage),
  [SystemParametersInfoW](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-systemparametersinfow),
  [EnumWindows](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-enumwindows),
  [SetUnhandledExceptionFilter](https://learn.microsoft.com/en-us/windows/win32/api/errhandlingapi/nf-errhandlingapi-setunhandledexceptionfilter),
  `WM_*` messages and `SetWindowPos`) and from black-box observations of the
  existing `host/playground.cpp` specimen in this repo. Those documentation
  URLs were not probed from the development sandbox; they name the public API
  contracts used, not a downloaded implementation.
* **No ReactOS source, translation, adaptation, icons, strings, assets or
  documentation was copied.** No leaked, decompiled or otherwise illegally
  obtained Windows code was used.
* The mod used by the first scenario is the repository's own
  `examples/visual-playground.wh.cpp`, unmodified. A second, **optional**
  scenario can load the public upstream mod `win81-taskbar-recreation`
  (`Windows 7/8/8.1 Taskbar Recreation`) from a user-supplied clone. That mod is
  GPL-3.0-or-later and is **not vendored**, copied or adapted here; the lab reads
  its source at the path you pass and reports build/init outcomes only. The PR-validation snapshot in
  `third_party/windhawk-pr-validation/` is a separate, explicitly attributed
  upstream component and is not used by this lab.
* Licensing is **not** asserted to be automatically resolved by this choice.
  Any doubt about third-party code, fonts or assets that may be added later must
  be raised with the maintainer before merging. If a future simulation needs
  undocumented internals or external code, stop and explain instead of inventing
  or copying an implementation.

## Install and run

```bash
./setup.sh                                   # Wine, MinGW, Xvfb, MinHook; once
python3 -m awt.desktop examples/visual-playground.wh.cpp
python3 -m awt.desktop --no-mod               # negative control, no mod loaded
python3 -m awt.cli desktop examples/visual-playground.wh.cpp \
    --settings enabled=false --no-mod-effect   # mod loaded, accent expected to be absent

# Optional trial of a real public mod from your own clone of windhawk-mods.
# The path below is only a local example; nothing is downloaded or vendored.
python3 tests/run_taskbar_probe.py --mod ~/src/windhawk-mods/mods/win81-taskbar-recreation.wh.cpp
python3 -m awt.desktop ~/src/windhawk-mods/mods/win81-taskbar-recreation.wh.cpp \
    --scenario taskbar-compat --no-mod-effect
```

Equivalently: `python3 -m awt.cli desktop <mod>`.

Exit codes: **0** all assertions passed, **1** build/run/assertion failure,
**2** usage error, **3** prerequisites missing (`not_executed`, no capture
substituted). Every attempt writes `.sandbox/desktop/<timestamp-id>/report.json`,
`host.log`, `mod.log`, `stubs.log`, `actions.txt`, `capture.bmp`, `capture.png`,
`xvfb.log` and `cleanup.log`. Wine prefixes are created fresh per attempt and
removed only after `wineserver -k`/`-w` completed; no global `pkill` is used.

### Surface profiles and shell-like details

Two profiles are available: `win10` (default for both scenarios) adds a
Windows-10-like Start/search strip, task buttons and a tray drawn with public
GDI calls, and `basic` keeps the earlier minimal strip. `--profile` overrides
the scenario default. The host logs the selected profile, a `EnumWindows` probe
of visible windows, `SHAppBarMessage(ABM_NEW/ABM_SETPOS/ABM_REMOVE)` results and
`SPI_GETWORKAREA` when available. All failures are written as
`[awt-desktop] error ...` lines and copied into `report.json: errors`; an
unhandled exception is logged through `SetUnhandledExceptionFilter` as
`unhandled-exception`. The `no_crash` assertion fails if either crash marker is
present.

In the `win10` profile, when the probe finds no existing `Shell_TrayWnd`, the
host registers a **synthetic child window class with that name inside
`awt-desktop.exe`**. It is our own GDI-drawn strip, not Explorer, not the real
shell, and not a shell replacement; the probe suppresses it when another window
already uses the class. `report.json: shell_class_emulation` records whether it
was used. Do not interpret `appbar` or class-name markers as shell integration:
they only describe calls made by this process.

## Architecture

| Layer | File | Responsibility |
| --- | --- | --- |
| Scene | `host/desktop.cpp` | Original Win32 surface (960×600), custom-drawn taskbar/notification area, labelled child windows, scripted actions, BMP capture, log markers. Compiled by `builder.build_desktop()`. |
| Orchestration | `awt/desktop.py` | Validates the scenario, builds the mod with `builder.build()`, starts Xvfb/prefix using `preview.display_session` and `preview._command`, applies timeouts, converts BMP via `preview.decode_capture`, evaluates assertions, cleans up in `finally`, writes the report. |
| CLI | `awt/cli.py` | `desktop` subcommand. |
| Runtime acceptance | `tests/run_desktop_tests.py` | Two real Wine runs with the existing demo mod (enabled and disabled), with pixel assertions and cleanup checks. |
| Optional real-mod probe | `tests/run_taskbar_probe.py` | Runs `taskbar-compat` with a user-supplied public taskbar mod; exit 0 = honest observation (including a clean refusal), 1 = crash/unexpected result, 3 = unavailable. |
| Unit tests | `tests/unit/test_desktop.py` | Action whitelist, marker parsing, assertion logic, isolated/not-executed paths, cleanup on timeout, CLI codes. Images there are generated fixtures, never presented as captures. |

The process is named **`awt-desktop.exe`**, not `explorer.exe`. This is a
deliberate decision: Wine has its own `explorer.exe`, a lookalike name would
invite the false conclusion that a mod was loaded into the Windows shell, and
the host Explorer is never replaced or modified. Custom window classes
(`AwtDesktopSurface`, `AwtDesktopWindow`) are not Explorer classes. The optional
`win10` profile can register a synthetic child class named `Shell_TrayWnd`
inside this process; even then the report states explicitly under
`process.explorer: false` that this is not Explorer or the Windows shell.

The scene and windows are children of one surface, so one `BitBlt` captures the
whole simulated desktop, including the taskbar and notification area. The mod is
loaded in-process with `LoadLibraryW`, then `Wh_ModInit`, `AwtModInitReturned`
(hook application) and `Wh_ModAfterInit` run in the same order as the visual
playground. Internal-symbol hooks remain unsupported by the shim and are listed
as `not_verifiable_here`; the lab never simulates their success.

### Action script

One action per line, `#` starts a comment. Whitelisted in Python and again in
the C++ host:

| Command | Effect |
| --- | --- |
| `create <id> <title> [heading] [body]` | labelled child window with custom title bar and content |
| `activate <id>` / `minimize <id>` / `restore <id>` / `close <id>` | Z-order/active state, visibility, destruction |
| `click <id> notice` | deterministic synthetic `WM_LBUTTONDOWN`/`WM_LBUTTONUP` on the scene's button |
| `key <id> TAB|ESC|SPACE|ENTER` | deterministic synthetic `WM_KEYDOWN`/`WM_KEYUP` |
| `notify on|off` | show/hide the simulated notification bubble |
| `input-click <x> <y>` / `input-key <VK>` | **attempts** real `SendInput` injection; recorded as reported, not assumed successful |
| `wait <ms>` / `capture` | pause or mark the scheduled capture |

The default `accent-notice` scenario creates `alpha` and `beta`, activates
`alpha`, clicks its notice button, minimizes and closes `beta`, then captures.
`taskbar-compat` performs a similar scene without accent-pixel assertions so a
real mod can be observed even when it cannot affect our process. The exact
heading drawn in `alpha` is the demo mod's stable hook target,
`"Windhawk visual playground"`. Adding a scenario means adding a validated
action list and its assertions to `SCENARIOS` in `awt/desktop.py`; no external
code or undocumented API is required.

## Reading the report

```json
"status": "passed",
"assertions": [
  {"id": "notice_toggled", "passed": true, "observed": true},
  {"id": "notice_visible", "kind": "pixels", "passed": true, "observed": 38608}
],
"verification": {
  "verified": ["mod DLL built with the bench shim", "scripted desktop actions executed ..."],
  "simulated": ["desktop surface, taskbar and notification area", "..."],
  "not_verifiable_here": ["real Explorer / Start menu / taskbar behaviour", "..."],
  "requires_real_windows": ["shell integration", "internal symbols", "..."]
}
```

Statuses: `passed` (scene, capture and every assertion succeeded), `assertion_failed`,
`run_failed` (including timeout or failed cleanup), `build_failed`, `error`,
`not_executed` (prerequisites missing). The legacy `verdict.level` ladder is
deliberately **not** modified: this report is additive evidence about a
simulated scene.

`verified` contains only operations observed in this run. `simulated` names the
parts that exist only in the lab. `not_verifiable_here` and
`requires_real_windows` are always present. In particular:

* A screenshot is not proof of correctness: with the demo disabled, the accent
  must be absent; with it enabled, blue pixels appear because the real
  `DrawTextW` inline hook ran **in this process**.
* The notification is drawn by the lab itself; toggling it proves the scripted
  interaction and capture path, not anything about a mod.
* `input-click`/`input-key` only report what `SendInput` returned. The default
  scenario uses deterministic Win32 messages and does not claim that real device
  input was verified.
* No mod is ever claimed to be loaded into `explorer.exe`, `twinui.pcshell.dll`
  or a real shell process. For `win81-taskbar-recreation` in particular,
  symbol hooks, resource downloads, Start-menu reconstruction and Explorer
  integration are `not_verifiable_here`: a build refusal, init refusal or
  missing symbol must be reported as such, never as a working recreation.
* `report.json: mod_analysis` is a **textual** heuristic. It may list strings
  from comments, and it never proves that a hook was applied.

## Tests

```bash
python3 tests/unit/run_unit.py desktop           # offline logic; always runnable
python3 tests/run_desktop_tests.py               # real Wine scene; exit 3 if unavailable
python3 tests/run_taskbar_probe.py                # real mod trial; exit 3 if unavailable
bash tests/run_tests.sh                          # adds the stage-4 runtime check
```

CI runs both the standalone desktop acceptance and `tests/run_tests.sh` on
`ubuntu-latest` after `./setup.sh`, uploads reports/logs/captures (never Wine
prefixes) and fails the job when the desktop stage fails or cannot execute.

In the development sandbox for this change Wine, MinGW and Xvfb are absent:
only the offline unit tests were executed there. The C++ scene has **not** been
compiled or run in that sandbox, and its runtime acceptance is reported as
`not_executed`, never as passed. The same is true of the real-mod probe: on
2026-11-25 `tests/run_taskbar_probe.py` returned **exit 3 / not_executed**
because MinGW, Wine, wineserver and Xvfb were missing. Its report recorded a
bounded textual analysis of `win81-taskbar-recreation` (including
`HookSymbols`, `SYMBOL_HOOK` and Explorer/PDB-related modules) as
`not_verifiable_here`; **no build, load, init, hook or screenshot was observed**.
That is a probe of availability and source metadata, not evidence that the
taskbar recreation works.
