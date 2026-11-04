# Execution backends (Feature 2)

The bench began with one way to run a mod: headless Wine on Linux. That answer is
honest, and it is narrow — Wine is not Windows, and its shell is not the Windows
shell. Feature 2 makes **"where did this actually run?"** part of the report, and
adds two more places a mod can really run.

Nothing here changes what a verdict means. A backend changes *where* the run
happened and *how much of the result can be trusted*; the verdict ladder
(`not_executed` → `build_failed` → `load_failed` → `crashed` → `loads_no_crash` →
`public_hooks_verified`) is shared code (`awt/verdict.py`) and is identical for
all three backends.

```
python3 -m awt.backends                     # what can THIS machine execute, and why not
python3 -m awt.backends --list-json         # the same, as JSON
python3 -m awt.backends --backend self-hosted-vm --mod examples/hello-log.wh.cpp
python3 -m awt.backends.windows_ci --selftest
```

## The three backends

| backend | where it executes | what it proves | what it still does not prove |
| --- | --- | --- | --- |
| `wine` | Linux + Xvfb + a per-repo Wine prefix | the mod compiles, loads, initialises, its public-API hooks fire, it does not crash | anything about real Windows: no Microsoft binaries, no real shell, internal symbols never resolved (`docs/limitations.md` items 1–3) |
| `windows-runner` | a native Windows process — a GitHub Actions runner or any Windows PC | the same, **on real Windows**: the DLL is loaded by a real Windows loader, the hooks are real function hooks in a real process | the mod is loaded by this bench's shim host, not by the Windhawk engine, so internal-symbol hooks stay `not_verifiable_here`; on GitHub runners the OS is Windows **Server**, so shell outcomes are *indicative* |
| `self-hosted-vm` | a Windows VM the user supplies, restored to a clean snapshot before each run | the same as `windows-runner`, plus a **client** Windows shell when the guest is a client edition, and reproducibility from the snapshot | it proves as much as the guest does: the shim host is still not the Windhawk engine, and the VM is only as clean as the snapshot |

Selection: `awt.backends.select(preferred=None)` returns the first backend that
reports itself available, and on Windows it tries `windows-runner` before `wine`.
Every refusal comes with a reason, which is also what lands in the report:

```
wine            unavailable
                reason: wine is not installed (looked for $AWT_WINE, /usr/lib/wine/wine64, wine64, wine)
windows-runner  unavailable
                reason: not running on Windows (this process is Linux/x86_64)
self-hosted-vm  unavailable
                reason: no VM configuration: copy configs/vm.example.yaml to configs/vm.local.yaml or set $AWT_VM_CONFIG
```

## What the report gains (additive only)

Every v1 key keeps its meaning. Two keys are added:

```jsonc
{
  "run": {
    "backend": "windows-runner",            // existing key, now names the backend
    "environment": {                        // NEW: where the run really happened
      "backend": "windows-runner",
      "os": "Windows",
      "os_version": "10.0.20348",
      "edition": "Windows Server 2022",
      "build_number": 20348,
      "product_type": "server",             // client | server | domain-controller
      "shell_profile": "server",            // client | server | wine | unknown
      "virtualization": "github-hosted runner VM",
      "vm_used": true,
      "snapshot_restored": null,            // self-hosted-vm: was the snapshot reverted?
      "runner": "Hosted Agent",
      "runner_image": "windows-2022 20260115.2",
      "arch": "AMD64",
      "hostname": null,
      "collected_by": "awt.backends.windows_ci",
      "collected_at": "2026-11-04T17:31:02Z",
      "seconds": 6.41,
      "notes": ["collected from RtlGetVersion, registry:InstallationType"],
      "caveats": ["the mod was loaded by the bench's own shim host, ..."]
    }
  },
  "verdict": {
    "level": "public_hooks_verified",       // unchanged semantics
    "verified": ["build", "load", "public API hooks"],
    "not_verifiable_here": ["internal-symbols: ?ShowStartView@XamlLauncher@@..."],
    "requires_real_windows": true,
    "indicative": [                          // NEW: caveats that qualify the level
      "shell-related outcomes are INDICATIVE: this backend runs on Windows Server (build 20348), ..."
    ]
  }
}
```

`notes` is the operational trail (what happened, what was slow, what failed).
`caveats` is what a reader must know before *interpreting* the verdict, and it is
the only list — besides the shell profile — that reaches `verdict.indicative`.
`apply_environment()` never writes to `verdict.level`, `verified`,
`not_verifiable_here` or `requires_real_windows`: where a run happened cannot
change what it proved.

### `shell_profile` and "indicative"

* `client` — a workstation Windows shell (Windows 7/8/10/11 client editions). No
  caveat is added: this is the environment mods target.
* `server` — Windows Server, including every GitHub-hosted Windows runner
  (`windows-latest` is build 20348, Server 2022). Server has no immersive
  taskbar, no Start-menu experience and a different explorer, so shell-related
  outcomes are **indicative**. `verdict.indicative` says so in words, with the
  build number.
* `wine` — Wine on Linux. Not indicative but *simulated*: the caveat says the
  shell is not Windows at all.
* `unknown` — the environment could not be identified (for example, the
  `windows-runner` backend asked on a Linux machine). Build **26100** is
  deliberately left `unknown` when `ProductType` is unavailable: it is both
  Windows 11 24H2 and Windows Server 2025, and guessing would be a lie in one
  direction or the other.

Detection order on Windows: `RtlGetVersion` (which, unlike `GetVersionEx`, does
not lie without a manifest) → the `Windows NT\CurrentVersion` registry key
(`ProductName`, `DisplayVersion`, `CurrentBuildNumber`, `InstallationType`) →
GitHub Actions variables (`RUNNER_OS`, `ImageOS`, `ImageVersion`, `RUNNER_NAME`).
Every helper is a pure function over strings, so all of it is unit-tested on
Linux (`tests/unit/test_backend_windows_ci.py`).

## The CI backend (`windows-runner`)

`.github/workflows/windows-ci.yml` compiles and runs a mod on `windows-latest`
and uploads `windows-report.json`, the backend capability table and a screenshot
as the `windows-report` artifact.

* Triggered by `workflow_dispatch` (choose the mod, the duration, whether to
  capture a screenshot), by pull requests that touch `awt/`, `shim/`, `host/`,
  `targets/`, `examples/` or `tests/`, and by pushes to `main`.
* A mod that fails to build, crashes or does not initialise does **not** fail the
  job. The verdict is the result; the job only fails when the harness itself
  breaks. The step summary states the level, the environment and every caveat.
* The toolchain: the workflow looks for MSYS2's `g++` (`C:\msys64\ucrt64\bin`,
  `C:\msys64\mingw64\bin`, …) and exports `AWT_CXX`/`AWT_CC`/`AWT_NM`/
  `AWT_CXXFILT`, which `awt/builder.py` honours before falling back to the
  `x86_64-w64-mingw32-*` cross prefix used on Linux.
* Screenshots use PowerShell `CopyFromScreen`. If the image is a single colour —
  what a service/session-0 desktop looks like — the report says so instead of
  presenting a black rectangle as evidence. If no image is written, the
  screenshot is reported `unavailable`.

Run it by hand on any Windows machine:

```powershell
python -m awt.backends.windows_ci --selftest
python -m awt.backends.windows_ci --mod examples\hello-log.wh.cpp --seconds 6 --screenshot --json report.json
python -m awt.backends.windows_ci --summary report.json   # Markdown for a CI summary
```

Exit codes follow the bench's convention: `0` = `loads_no_crash` or better, `1` =
`build_failed`/`load_failed`/`crashed`, `3` = `not_executed`.

## The self-hosted VM backend

See **`docs/vm-protocol.md`** for the protocol and **`docs/reference-vms.md`** for
which VMs are worth building and how to snapshot them. The short version:

```bash
cp configs/vm.example.yaml configs/vm.local.yaml   # git-ignored: no image paths in git
$EDITOR configs/vm.local.yaml                      # hypervisor commands, shared folder, guest
python3 -m awt.backends --backend self-hosted-vm --mod examples/taskbar-clock.wh.cpp --screenshot
```

Per run: revert the clean snapshot → start the VM → wait for the guest agent's
heartbeat → the agent runs the staged `host.exe` with the staged `mod.dll` →
collect `result.json`, logs and screenshot → stop the VM. Timeouts are generous
(900 s to boot, 600 s per job by default) because a cold Windows 11 21H2 VM
really can need ten minutes. Every failure mode ends in `not_executed` with the
timeout or the command that failed — never in a partial success.

No credentials, no VM images and no disk paths are stored in this repository:
`configs/vm.local.yaml` is ignored by the `*.local.yaml` pattern, and anything
else goes through `$AWT_VM_CONFIG`.

## Verification status

What has actually been executed, and where:

* **On Linux (this sandbox)** — 66 unit tests for the backend contract, the
  shared verdict module, the Windows environment-detection helpers and the whole
  VM protocol. The VM protocol is driven end to end by
  `tests/unit/fixtures/fake_vm.py`, a fake hypervisor *and* fake in-guest agent
  that also reproduces the failure modes: a VM that never boots, a guest that
  heartbeats but never finishes, a failed snapshot revert, a guest crash, a
  truncated screenshot, a Server guest. `python3 tests/unit/run_unit.py` →
  182 passed / 0 failed / 2 skipped (system Python), 184 / 0 / 0 in the
  project's optional-dependency venv.
* **On a GitHub Windows runner** — see the `windows-ci` check for this branch.
  That run is the only evidence about native execution; nothing native was
  executed in the Linux sandbox, and no claim in this document depends on it.
* **On a real VM** — not executed by this project. The backend is exercised
  against the fake agent; a user's QEMU/VirtualBox/Hyper-V machine is theirs to
  configure, and the report will say what the guest reported, including when the
  guest never answered.

## What this feature does not do

* It does not install or drive the real Windhawk engine. A mod that only resolves
  internal symbols at runtime is still `not_verifiable_here` on real Windows —
  combine it with the Feature 1 symbol matrix (`python3 -m awt.symbols`), which
  answers that question statically, per Windows build.
* It does not inject into `explorer.exe` or `twinui.pcshell.dll`. The bench's host
  loads the mod in its own process; a taskbar mod's *effect on the taskbar* is
  therefore still not observed, on any backend.
* It does not make GitHub runners a substitute for client Windows. Server 2022 is
  a real Windows kernel and a real loader — and the wrong shell.
