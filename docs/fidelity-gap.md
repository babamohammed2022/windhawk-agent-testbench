# Fidelity gap: real Windhawk vs this testbench

Status: reconnaissance, written **before** the changes in this branch. Every row
below was derived from reading the files named in the last column or from a
command actually run in this sandbox on 2026-11-26. It is a work list, not a
claim that the gap is closed.

Baseline observed here:

| Command | Result |
|---|---|
| `./setup.sh` | exit 41 — `apt-get` cannot install `wine64` in this sandbox; no Wine, MinGW or Xvfb |
| `./run.sh examples/hello-log.wh.cpp` | exit 3, `verdict.level = not_executed`, "the mod was neither compiled nor run" |
| `./tests/run_tests.sh` | exit 2 — stage 1 (pure-Python unit tests) passed, 18 Wine acceptance checks **SKIPPED**, never passed |
| `python3 -m awt.conformance` | exit 0 — 16 internal API functions implemented, 10 stubs, 0 missing (cached Windhawk 1.7.3 headers) |

Nothing in this document may be read as "the mod works on Windows". The purpose
of the table is to make the difference explicit so that an agent can decide when
to stop and say *not verifiable here*.

| Real Windhawk behaviour | Testbench behaviour today (evidence) | Proposed action |
|---|---|---|
| Mod metadata drives process selection, compiler options, architecture and localised names. | `awt/metadata.py` keeps `@include`/`@exclude` as raw strings, flattens all other tags into `raw`, and parses settings as a flat map. `@name:it-IT`, nested arrays and `Setting[0].Name` have no representation. `docs/official/windhawk_api.h` documents printf-style indexed setting names. | Parse every documented tag plus localisations; parse the settings YAML into a tree, flatten indexed paths for the shim, and record what could not be parsed. |
| `@include` wildcards and full paths are matched against the process name/path; `@exclude` vetoes a match. | `_auto_mode()` in `awt/runner.py` only checks for the substring `explorer`; every other include goes to `testapp.exe`. `@exclude` is parsed then unused. | Add explicit, tested include/exclude matching and record the chosen target, the matched pattern and the fallback reason in the report. |
| `@compilerOptions` are passed to the real compiler; `-l` libraries and `-std=` are meaningful. | `awt/builder.py` appends the raw tokens to the link command. Anything unsupported is either ignored by the compiler or produces an opaque build error; no report field distinguishes it. The compiler version and the effective flags are not recorded. | Classify flags into accepted / rejected with reasons, honour safe `-l`, `-D`, `-std=` and `-Wl,` forms, report every rejection and record compiler version and effective flags. Never silently drop a flag. |
| Windhawk compiles with its own Clang/lld/libc++ toolchain for x86 and x86-64 (and arm64 on supported hosts). | `awt/builder.py` uses `x86_64-w64-mingw32-g++/gcc` (or a native MinGW equivalent), C++20, x86-64 only. Other `@architecture` values only produce a static-check warning. | Keep the working MinGW path, record exactly which compiler and flags ran, mark other architectures *compile-only/unsupported* rather than pretending coverage; document llvm-mingw as an alternative, not an installed prerequisite. |
| `Wh_Log` prints engine-formatted messages; diagnostics give `file:line:column`. | `awt/builder.py` parses `file:line:col: error:` into structured errors, but lines without that shape become a single `line: 0` entry. | Preserve compiler diagnostics verbatim and keep the `file:line:col` shape; record the raw compiler output when structured parsing fails. |
| `Wh_SetFunctionHook` / `Wh_ApplyHookOperations` install real inline hooks in the injected process. | `shim/shim.cpp` queues operations in `g_pending` and applies them with MinHook; `AwtAppliedHookCount` reports the number. Verified in the existing acceptance suite on a Wine machine, **not** in this sandbox (see baseline). | Leave the working path alone. Add evidence about queued/applied/failed hooks to the report instead of inferring success from a mod-owned log alone. |
| `Wh_RemoveFunctionHook` and unloading restore the original bytes. | `shim/shim.cpp` calls `MH_RemoveHook`, but the host's uninit path is not checked for residual hooks and `MH_Uninitialize` is never called. | Document as simulated/partial: no unload-integrity assertion is emitted, so no claim is made here. |
| `Wh_FindFirstSymbol`/`Next`/`Close` and `WindhawkUtils::HookSymbols` resolve PDB/export symbols, with optional symbol caching. | All four fail on purpose and log `[SHIM] symbol lookup not available in sandbox`; `awt/symbols/` checks **names** offline against public PDBs, which is not an execution. | Do not fake symbol resolution under Wine (it would create false positives). Add a clearly-labelled, opt-in configured symbol table only if the report says `simulated`; otherwise keep `not_verifiable`. Extend the offline name matrix. |
| Settings are a typed tree (groups, arrays, dynamic-select entries); `Wh_GetIntSetting(L"a[%d].b", i)` addresses nested values. | The shim parses a flat JSON object; builder writes flattened defaults. Indexed reads work only when the key was already in the JSON. | Parse nested YAML in Python, flatten indexed paths, and say in the report which reads were resolved from the block and which were absent. |
| Storage is per-mod and persistent across restarts; `Wh_ListValues` exists in some mod Idioms but is not part of the cached 1.7.3 API. | `shim/shim.cpp` stores `I`/`S`/`B` values in one `values.txt` under `.sandbox/storage/<id>/`; no enumeration API. | Keep the round-trip; document that enumeration/`Wh_ListValues` is **not in the official header** used here and is therefore not implemented, not "stubbed". |
| A tool mod (`WH_TOOL_MOD`, `@architecture` tool process, `@include`/`@exclude`) initialises in a dedicated tool process. | There is no tool-process mode; `@include explorer.exe` is mapped to the in-process self host. | Document as `not_verifiable` with the precise missing mechanism; add a static detector so agents are told early. |
| The engine calls `Wh_ModInit` → `Wh_ModAfterInit` → settings changes → `Wh_ModBeforeUninit` → `Wh_ModUninit`. | `host/host.cpp` calls these exports in that order in self mode; markers are parsed by `awt/verdict.py`. Reload is only a notification: no settings block is re-read, no hooks are removed. | Record which callbacks were **observed**, and label the reload path as simulated. Do not claim a full engine lifecycle. |
| Real targets include Explorer, the shell, common dialogs, COM/WinRT, DWM, tooltips and menus. | `targets/` has `testapp.exe`, `trayapp.exe`, `enumwins.exe`; `host/desktop.cpp` draws a Win10-like strip with synthetic `Shell_TrayWnd` classes; `docs/limitations.md` explains why Explorer cannot be loaded here. | Add stand-ins only as declared, labelled surfaces; keep every shell outcome `simulated`. No new C++ target may be claimed without a real Wine build, which this sandbox cannot perform. |
| Injection into a live target; mod unload/reload does not leave hooks behind. | `docs/limitations.md` records two observed Wine 10.0 failures (remote `LoadLibraryW` execute-page fault, ignored `AppInit_DLLs`); the runner falls back to self mode and records a reason. | Keep the fallback and its reason; never relabel it as injection. Document hook-integrity checks as future work. |
| A report separates verified, simulated and not verifiable, and lists APIs, hooks, libraries, target choice and warnings. | `awt/report.py` `schema: 1` carries `verdict.verified`, `verdict.not_verifiable_here`, `hook_coverage`, `crash`, `static_checks`, `include_analysis` and `symbol_matrix` (when requested). There is no capability matrix, API inventory, toolchain record or fidelity-warning list. | Add an **additive** schema-1 section per capability plus API/target/toolchain evidence. Never change `verdict.level` from these descriptive sections. |
| An agent reading the report knows when to stop and declare something non-verifiable. | `AGENTS.md` explains the verdict ladder and several add-on sections, but gives no workflow for a capability matrix or for new static warnings. | Extend the agent workflow with an explicit "read capabilities → stop when not_verifiable" rule and keep the warning list short and literal. |

## Explicitly impossible here (do not force)

The following are recorded in `docs/limitations.md` with reasons and, where one
exists, an alternative: loading into `explorer.exe`, real PDB symbol lookup,
remote injection, `AppInit_DLLs`, native Windows verification, GPU-accurate
rendering, and the tool-process lifecycle. Implementing an emulated version of
any of them must be labelled `simulated` in the report; it must never upgrade a
verdict.


## Status after this branch (2026-11-26)

| Gap | Status now |
|---|---|
| Metadata tags, localisations, wildcard/full-path include + exclude, nested/indexed settings | Addressed in `awt/metadata.py` and `awt/targeting.py`, with offline tests. Settings values are still `simulated` at runtime. |
| `@compilerOptions` classification, accepted flags reaching compiler/linker, compiler version record | Addressed in `awt/compileroptions.py` and `awt/builder.py`; rejected flags are warnings. No claim of library resolution without a real build. |
| Capability matrix, API inventory, fidelity warnings, stand-in rationale | Additive sections in `awt/fidelity.py` and `awt/report.py`; `verdict.level` untouched. |
| Static detection of unknown/stub APIs and Wine-unverifiable surfaces | `awt/staticchecks.py`; descriptive only. |
| Representative examples | `settings-array-demo`, `tool-mod-demo`, `symbol-lookup-demo` added beside the existing mods. |
| Shim API completeness (`Wh_ListValues`, `Wh_GetModName`, `Wh_IsLocalMod`, `Wh_SetFunctionHookDirect`, configurable symbol table, tool mod) | **Open and documented as not implemented / not verifiable.** The requested names are absent from the cached official headers; inventing them would create false positives. |
| New C++ targets, hook instrumentation, real unload/reload assertions, screenshot/timing/memory capture extension | **Open.** This sandbox cannot compile or run C++, so no untested executable was added. |
| x86 / arm64 compilation, llvm-mingw switch | **Open.** Only x86-64 MinGW is present; the report records the compiler that actually answered. |
| Wine acceptance checks | **Not executed here.** Unit tests pass; the 18 Wine checks remain skipped until a machine with Wine/MinGW/Xvfb runs `./tests/run_tests.sh`. |


## Shell-recreation follow-up (same branch, 2026-11-26)

| Gap | Status now |
|---|---|
| Shell-recreation mods were mapped to a generic stand-in | `awt/shellsurfaces.py` detects per-surface coverage and produces a concrete `verification_plan`; `awt/targeting.py` handles Start/XAML includes and explorer+control multi-includes |
| Static checks fired on base64 resource blobs and on deprecated `WindhawkUtils::Wh_SetFunctionHookT` | Both fixed: long lines are excluded from pattern matching and the deprecated alias resolves to `InternalWh_SetFunctionHook` |
| Standard MinGW import libraries used by real recreations (`-liphlpapi`, `-lwlanapi`, `-luuid`) produced false "unverified library" warnings | Added to the known import-library set; only a real linker can still prove resolution |
| `not_executed` reports printed "real Windows: not required" even for shell mods | The summary now prints `unknown (nothing was executed here)` |
| C++/Wine runtime fidelity for shell surfaces, internal symbol hooks and real Start menu/flyout behaviour | **Still open and `not_verifiable`.** No untested target or fake symbol table was added. |
