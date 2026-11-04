# A Modest Testbench for Windhawk Mods
### (working title: `automatic-windhawk-testing`; suggested GitHub name: `modest-windhawk-testbench`)

An unofficial, provisional, Linux-only Wine bench where AI agents (and humans) can
compile, load and exercise a Windhawk mod (`.wh.cpp`) end-to-end, and receive a
structured JSON report that separates **verified** from **simulated** from
**not verifiable without real Windows**. It claims only what it has executed.

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

## Layout
- `awt/` — Python tool: metadata parser, dependency analyzer, builder, runner, report,
  conformance checker (`python3 -m awt.conformance`), environment probe (`awt/env.py`),
  dependency-free YAML subset (`awt/yamlmini.py`), PE reader (`awt/pe.py`), PDB reader
  (`awt/pdb/`) and the symbol matrix (`awt/symbols/`, `python3 -m awt.symbols`).
- `configs/symbol-matrix.yaml` — the Windows builds Feature 1 checks against.
- `shim/` — re-implementation of the Windhawk engine API (`InternalWh_*`) on top of
  MinHook; compiled into every mod DLL. Official headers in `shim/include/`.
- `host/` — loader: in-process self mode, or injection into a target process.
- `targets/` — `testapp.exe` (generic Win32 hook target), `trayapp.exe` (tray client),
  `enumwins.exe` (window-class probe).
- `examples/`, `tests/shim_conformance/` — mods used as acceptance tests.
- `docs/` — `links.md`, `limitations.md`, `shim-status.md`, `symbol-matrix.md`,
  official headers cache.
- `tests/unit/` — Wine-free unit tests (`python3 tests/unit/run_unit.py`), including an
  end-to-end symbol-matrix run against a local fake symbol server.

## How a mod is loaded
1. metadata + YAML settings parsed (`awt/metadata.py`);
2. mod compiled as x86-64 DLL with `-DWH_MOD`, linked with the shim and MinHook,
   lifecycle exports generated from `nm`+`c++filt` into a `.def`;
3. Xvfb + clean per-repo Wine prefix (`.sandbox/prefix`);
4. target chosen from `@include` (Wine-native program when possible, else sandbox
   testapp or the simulated self-host; every fallback is recorded with a reason);
5. mod initialised, hooks applied, target driven so hooks fire, teardown, logs and
   optional screenshots collected into `.sandbox/report.json`.

See `AGENTS.md` for agent workflow and `docs/limitations.md` for what Wine cannot do.
