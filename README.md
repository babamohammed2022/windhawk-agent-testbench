# A Modest Testbench for Windhawk Mods
### (working title: `automatic-windhawk-testing`; suggested GitHub name: `modest-windhawk-testbench`)

An unofficial, provisional bench where AI agents (and humans) can compile, load and
exercise a Windhawk mod (`.wh.cpp`) end-to-end, and receive a structured JSON report
that separates **verified** from **simulated** from **not verifiable without real
Windows**. It claims only what it has executed, and since Feature 2 it also says
*where*: the default backend is headless Wine on Linux, and the same report can be
produced by a native Windows CI runner or by a Windows VM you own.

*This project is not Windhawk, is not affiliated with it, and does not pretend to
replace testing on a real Windows machine.*

It is NOT Windhawk: no real service, no hooking of internal shell components. A green
result means *compiles, loads, public-API hooks behave, no crash in the sandbox*.

## Quick start
```bash
./setup.sh                      # once; idempotent
./run.sh examples/hello-log.wh.cpp
cat .sandbox/report.json
./tests/run_tests.sh            # the tool's own acceptance suite
```

## Symbol matrix — shell mods, checked on Linux (Feature 1)
The bench cannot hook `twinui.pcshell.dll` under Wine, but it *can* answer the
question that decides whether such a mod does anything at all: does the internal
symbol it asks for exist in the Windows build the user runs?

```bash
python3 -m awt.symbols examples/internal-symbol-demo.wh.cpp     # CLI table
python3 -m awt.symbols mod.wh.cpp --json .sandbox/symbols.json  # report section
python3 -m awt.symbols mod.wh.cpp --list                        # no network at all
python3 -m awt.cli run mod.wh.cpp --symbols-json .sandbox/symbols.json
```

It extracts the symbols and target modules from the mod source, resolves each
module + build to a PE signature (pinned table or winbindex), downloads the PE
and its PDB from Microsoft's public symbol server into the git-ignored
`.cache/`, parses both with the pure-Python readers in `awt/pe.py` and
`awt/pdb/`, and reports per symbol per build: `found` / `missing` / `ambiguous`,
or `unknown` when the check could not be performed — never `found`. Builds are
configured in `configs/symbol-matrix.yaml` (Windows 10 1507 and 22H2, Windows 11
21H2/23H2/24H2/25H2 by default). A `found` is a statement about **names**: it is
not an execution and it does not change the verdict ladder.
Details, evidence and limits: [`docs/symbol-matrix.md`](docs/symbol-matrix.md).

## Real Windows backends (Feature 2)
The same report, three places it can come from. `run.environment` records the OS,
edition, build number, product type, whether a VM was used and whether a snapshot
was restored; `verdict.indicative` carries every caveat that qualifies the level.

```bash
python3 -m awt.backends                                   # what can THIS machine run, and why not
python3 -m awt.backends --list-json                       # the same, as JSON
python3 -m awt.backends --backend self-hosted-vm --mod mod.wh.cpp --screenshot
python3 -m awt.backends.windows_ci --selftest             # native Windows backend
```

- **`wine`** — the existing path, wrapped not rewritten: Linux + Xvfb + a per-repo
  prefix. Shell outcomes stay *simulated*.
- **`windows-runner`** — compiles and runs the mod in a native Windows process.
  `.github/workflows/windows-ci.yml` does this on `windows-latest` and uploads the
  report plus a screenshot as the `windows-report` artifact. GitHub runners are
  Windows **Server** (build 20348), so shell-related outcomes are marked
  **indicative**, never verified. A mod that crashes does not fail the job: the
  verdict lives in the artifact.
- **`self-hosted-vm`** — drives a Windows VM you already have (QEMU/VirtualBox/
  Hyper-V), reverting a clean snapshot before each run, over a shared folder and a
  small in-guest PowerShell agent (`vm/agent/awt_agent.ps1`). Configuration lives in
  `configs/vm.local.yaml` (git-ignored; copy `configs/vm.example.yaml`): no VM image,
  disk path or credential is ever committed.

Even on real Windows the mod is loaded by this bench's shim host, not by the Windhawk
engine, so internal-symbol hooks stay `not_verifiable_here` — pair this with the symbol
matrix above. Details: [`docs/backends.md`](docs/backends.md),
[`docs/vm-protocol.md`](docs/vm-protocol.md),
[`docs/reference-vms.md`](docs/reference-vms.md).

## Visual comparison (Feature 3)
"Does the screen look right?" as numbers, coordinates and a picture of *where* the
difference is — with an explicit statement of what those numbers cannot prove.
Descriptive, never a verdict: the section is added next to `verdict.level` and never
changes it.

```bash
python3 -m awt.visualdiff shot.png reference.png
python3 -m awt.visualdiff shot.png win11.png win7.png --diffmap diff.png --json out.json
python3 -m awt.visualdiff shot.png --mod examples/taskbar-clock.wh.cpp --roi 0,980,1920,120
python3 -m awt.visualdiff shot.png reference.png --report .sandbox/report.json
```

Per-pixel statistics (identical / within-tolerance / differing, mean, max, RMSE,
histogram), SSIM, and divergent **regions** with box, area, worst delta and centroid.
Similarity floors alone hide small sharp changes — a window moved by two pixels keeps
98.7 % of the image similar — so two ceilings (`max_differing_ratio`,
`max_region_area`) run in the other direction and the report shows which rule decided.
Statuses are `match` / `mismatch` / `incomparable` / `unavailable` (exit 0/1/3/3):
different resolutions are *incomparable* rather than silently rescaled, and an
unreadable image is never presented as a difference of zero. No Pillow, no numpy —
`awt/png.py` reads and writes PNG itself, and with numpy installed the SSIM uses the
standard 11×11 Gaussian window instead of the documented 8×8 block fallback.
Reference screenshots are images of Microsoft interfaces and are never committed.
Details: [`docs/visual-diff.md`](docs/visual-diff.md).

## Layout
- `awt/` — Python tool: metadata parser, dependency analyzer, builder, runner, report,
  conformance checker (`python3 -m awt.conformance`), environment probe (`awt/env.py`),
  dependency-free YAML subset (`awt/yamlmini.py`), PE reader (`awt/pe.py`), PDB reader
  (`awt/pdb/`), the symbol matrix (`awt/symbols/`, `python3 -m awt.symbols`), the shared
  marker/verdict logic (`awt/verdict.py`), the execution backends (`awt/backends/`,
  `python3 -m awt.backends`), the dependency-free PNG reader/writer (`awt/png.py`) and
  the visual comparison (`awt/visualdiff/`, `python3 -m awt.visualdiff`).
- `configs/symbol-matrix.yaml` — the Windows builds Feature 1 checks against.
- `configs/visual.example.yaml` — template for visual thresholds and reference sets
  (copy to the git-ignored `configs/visual.local.yaml`, or put `<mod>.visual.yaml`
  next to the mod).
- `configs/vm.example.yaml` — template for the self-hosted VM backend (copy to the
  git-ignored `configs/vm.local.yaml`).
- `vm/agent/awt_agent.ps1` — the in-guest agent: plain PowerShell 5.1, no network, no
  credentials.
- `.github/workflows/` — `ci.yml` (ubuntu, Wine acceptance suite) and `windows-ci.yml`
  (native Windows run, report + screenshot artifacts).
- `shim/` — re-implementation of the Windhawk engine API (`InternalWh_*`) on top of
  MinHook; compiled into every mod DLL. Official headers in `shim/include/`.
- `host/` — loader: in-process self mode, or injection into a target process.
- `targets/` — `testapp.exe` (generic Win32 hook target), `trayapp.exe` (tray client),
  `enumwins.exe` (window-class probe).
- `examples/`, `tests/shim_conformance/` — mods used as acceptance tests.
- `docs/` — `links.md`, `limitations.md`, `shim-status.md`, `symbol-matrix.md`,
  `backends.md`, `vm-protocol.md`, `reference-vms.md`, `visual-diff.md`, official
  headers cache.
- `tests/unit/` — Wine-free unit tests (`python3 tests/unit/run_unit.py`), including an
  end-to-end symbol-matrix run against a local fake symbol server, an end-to-end VM
  protocol run against a fake hypervisor and fake guest agent
  (`tests/unit/fixtures/fake_vm.py`), and visual-comparison tests on generated
  screenshots (`tests/unit/fixtures/images.py`) including a byte-for-byte PNG
  cross-check against Pillow when it is installed.

## How a mod is loaded (Wine backend)
1. metadata + YAML settings parsed (`awt/metadata.py`);
2. mod compiled as x86-64 DLL with `-DWH_MOD`, linked with the shim and MinHook,
   lifecycle exports generated from `nm`+`c++filt` into a `.def`;
3. Xvfb + clean per-repo Wine prefix (`.sandbox/prefix`);
4. target chosen from `@include` (Wine-native program when possible, else sandbox
   testapp or the simulated self-host; every fallback is recorded with a reason);
5. mod initialised, hooks applied, target driven so hooks fire, teardown, logs and
   optional screenshots collected into `.sandbox/report.json`.

See `AGENTS.md` for agent workflow and `docs/limitations.md` for what Wine cannot do.
