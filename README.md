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

## Layout
- `awt/` — Python tool: metadata parser, dependency analyzer, builder, runner, report,
  conformance checker (`python3 -m awt.conformance`).
- `shim/` — re-implementation of the Windhawk engine API (`InternalWh_*`) on top of
  MinHook; compiled into every mod DLL. Official headers in `shim/include/`.
- `host/` — loader: in-process self mode, or injection into a target process.
- `targets/` — `testapp.exe` (generic Win32 hook target), `trayapp.exe` (tray client),
  `enumwins.exe` (window-class probe).
- `examples/`, `tests/shim_conformance/` — mods used as acceptance tests.
- `docs/` — `links.md`, `limitations.md`, `shim-status.md`, official headers cache.

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
