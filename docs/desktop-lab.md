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
  `WM_*` messages and `SetWindowPos`) and from black-box observations of the
  existing `host/playground.cpp` specimen in this repo. Those documentation
  URLs were not probed from the development sandbox; they name the public API
  contracts used, not a downloaded implementation.
* **No ReactOS source, translation, adaptation, icons, strings, assets or
  documentation was copied.** No leaked, decompiled or otherwise illegally
  obtained Windows code was used.
* The mod used by the first scenario is the repository's own
  `examples/visual-playground.wh.cpp`, unmodified. The PR-validation snapshot in
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
```

Equivalently: `python3 -m awt.cli desktop <mod>`.

Exit codes: **0** all assertions passed, **1** build/run/assertion failure,
**2** usage error, **3** prerequisites missing (`not_executed`, no capture
substituted). Every attempt writes `.sandbox/desktop/<timestamp-id>/report.json`,
`host.log`, `mod.log`, `stubs.log`, `actions.txt`, `capture.bmp`, `capture.png`,
`xvfb.log` and `cleanup.log`. Wine prefixes are created fresh per attempt and
removed only after `wineserver -k`/`-w` completed; no global `pkill` is used.

## Architecture

| Layer | File | Responsibility |
| --- | --- | --- |
| Scene | `host/desktop.cpp` | Original Win32 surface (960×600), custom-drawn taskbar/notification area, labelled child windows, scripted actions, BMP capture, log markers. Compiled by `builder.build_desktop()`. |
| Orchestration | `awt/desktop.py` | Validates the scenario, builds the mod with `builder.build()`, starts Xvfb/prefix using `preview.display_session` and `preview._command`, applies timeouts, converts BMP via `preview.decode_capture`, evaluates assertions, cleans up in `finally`, writes the report. |
| CLI | `awt/cli.py` | `desktop` subcommand. |
| Runtime acceptance | `tests/run_desktop_tests.py` | Two real Wine runs with the existing demo mod (enabled and disabled), with pixel assertions and cleanup checks. |
| Unit tests | `tests/unit/test_desktop.py` | Action whitelist, marker parsing, assertion logic, isolated/not-executed paths, cleanup on timeout, CLI codes. Images there are generated fixtures, never presented as captures. |

The process is named **`awt-desktop.exe`**, not `explorer.exe`. This is a
deliberate decision: Wine has its own `explorer.exe`, a lookalike name would
invite the false conclusion that a mod was loaded into the Windows shell, and
the host Explorer is never replaced or modified. Custom window classes
(`AwtDesktopSurface`, `AwtDesktopWindow`) are not `Shell_TrayWnd` or any other
Explorer class. The report states this explicitly under `process.explorer:
false`.

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
`alpha`, clicks its notice button, minimizes and closes `beta`, then captures. The exact
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
  or a real shell process.

## Tests

```bash
python3 tests/unit/run_unit.py desktop           # offline logic; always runnable
python3 tests/run_desktop_tests.py               # real Wine scene; exit 3 if unavailable
bash tests/run_tests.sh                          # adds the stage-4 runtime check
```

CI runs both the standalone desktop acceptance and `tests/run_tests.sh` on
`ubuntu-latest` after `./setup.sh`, uploads reports/logs/captures (never Wine
prefixes) and fails the job when the desktop stage fails or cannot execute.

In the development sandbox for this change Wine, MinGW and Xvfb are absent:
only the offline unit tests were executed there. The C++ scene has **not** been
compiled or run in that sandbox, and its runtime acceptance is reported as
`not_executed`, never as passed.
