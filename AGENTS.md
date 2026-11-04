# AGENTS.md — A Modest Testbench for Windhawk Mods (`automatic-windhawk-testing`)

Instructions for AI agents using this repository. Imperative, short, unambiguous.

## 1. Workflow
1. `./setup.sh` once per sandbox (idempotent; installs Wine/MinGW/Xvfb, vendors MinHook,
   caches the official Windhawk headers in `docs/official/`).
2. `./run.sh path/to/mod.wh.cpp [--seconds N] [--mode auto|self|inject] [--target x.exe]`
3. Read `.sandbox/report.json` (human summary is printed). History accumulates in
   `.sandbox/history/`. Screenshots (optional): `--seconds N` plus runner flag in code.

## 2. Reading the verdict — the one rule that matters
Never tell the user a mod "works" based on this tool alone.
Report exactly the two lists `verdict.verified` and `verdict.not_verifiable_here`, plus
`verdict.requires_real_windows`. Levels, weakest to strongest: `build_failed`,
`load_failed`, `crashed`, `loads_no_crash`, `public_hooks_verified`. Even
`public_hooks_verified` means only: compiles, loads, public-API hooks fired, no crash
**in the Wine sandbox**.

## 3. Troubleshooting table
| Symptom | Likely cause | Action |
|---|---|---|
| build error "undefined reference to Wh_X" / missing declaration | shim lacks the function | add it to `shim/shim.cpp` (match `docs/official/windhawk_api_internal.h`), re-run, add regression test |
| `Wh_ModInit` returns FALSE (report `init_returned: false`, level `load_failed`) | mod refused the environment (e.g. not its target process, defensive checks) | read the mod log lines; do not "fix" the mod to please the sandbox unless the mod is objectively wrong |
| DLL load failure / garbled exports | 32 vs 64-bit mismatch, or missing `.def` alias | check `@architecture`; builder generates exports via `nm`+`c++filt` — inspect `.sandbox/build/<id>/exports.def` |
| "wine: created the configuration directory" then nothing | prefix first-run slow | wait; prefix lives in `.sandbox/prefix` |
| blank screenshots / no windows | Xvfb not running | runner starts Xvfb per run; check `DISPLAY` and `/tmp/.X11-unix` |
| run exceeds `seconds+60` | target hung or winedbg attached | CrashHandler is disabled by setup; `pkill winedbg`; inspect `.sandbox/logs/run-*.log` |
| hooks applied but `hooks_fired` empty | target never calls that API | drive the API yourself (host self-mode does generic activity; add to `host/host.cpp` if needed) |
| crash inside shim vs inside mod | backtrace in run log | shim frames contain `shim.cpp`; mod frames contain the mod's functions; fix the right side |
| `nullptr_t has not been declared` | official utils header vs libstdc++ | builder force-includes `shim/include/awt_compat.h`; keep it |

## 4. Internal-symbol mods (HookSymbols / Wh_FindFirstSymbol / twinui.pcshell.dll ...)
Do not try to force them under Wine. Produce the best code, list the exact symbols and
DLLs in `verdict.not_verifiable_here`, and tell the user what to check on real Windows
(hook installed? original called? no deadlock on loader lock?). The shim fails those
calls on purpose and logs `[SHIM] symbol lookup not available in sandbox`.

Then run the symbol matrix — it works without Windows and answers the first question a
shell mod fails on:

```bash
python3 -m awt.symbols path/to/mod.wh.cpp                     # table on stdout
python3 -m awt.symbols path/to/mod.wh.cpp --list              # extraction only, no network
python3 -m awt.symbols path/to/mod.wh.cpp --json .sandbox/symbol-matrix.json
python3 -m awt.cli run path/to/mod.wh.cpp --symbols-json .sandbox/symbol-matrix.json
```

Read the outcomes literally and quote them literally:
* `found` — that name exists in the public PDB of that exact binary. Say "the symbol
  exists on build X"; never "the hook works". Confidence tells you how the names were
  compared (`exact` = byte-identical like the engine does, `structural` = decoded
  decorated name, `partial` = name only).
* `missing` — the PDB was read and the name is not in it. This is actionable: the mod is
  inert on that build; check whether it marks the symbol `optional`.
* `ambiguous` — several candidates, or a case-only difference (Windhawk would not match).
* `unknown` — the check did not happen (no network, no signature, module absent from that
  build, unreadable PDB, module not resolvable from the source). Report it as unknown.
Exit codes: 0 at least one cell resolved, 3 nothing could be checked, 2 usage error.
Full documentation, evidence and limits: `docs/symbol-matrix.md`.

## 4b. Never commit downloaded binaries or generated caches
PEs, PDBs, winbindex payloads and parsed symbol indexes live in `.cache/` (git-ignored).
Reference clones and the venv live in `.sandbox/` (git-ignored). Machine-specific matrix
overrides go in `configs/symbol-matrix.local.yaml`. Only `configs/symbol-matrix.yaml`,
which holds public metadata (version strings, PE timestamps and sizes), is committed.

## 4c. Choosing an execution backend (and not over-claiming from it)
```bash
python3 -m awt.backends                                # availability + reasons, per backend
python3 -m awt.backends --backend windows-runner --mod mod.wh.cpp --screenshot
python3 -m awt.backends --backend self-hosted-vm --mod mod.wh.cpp   # needs configs/vm.local.yaml
python3 -m awt.backends.windows_ci --selftest          # what a Windows box can do here
```
Read `run.environment` before you quote any result:
* `shell_profile: client` — a workstation Windows shell. The strongest environment this
  bench can reach (self-hosted VM with a client edition).
* `shell_profile: server` — Windows Server, including **every GitHub-hosted Windows
  runner**. `verdict.indicative` says so in words. Never write "verified on Windows" for
  a taskbar/Start-menu/notification mod from a CI run; write "compiles, loads and does
  not crash on Windows Server 2022; the shell itself was not exercised".
* `shell_profile: wine` — simulated. Not indicative, not verified: it is not Windows.
* `shell_profile: unknown` — the environment could not be identified. Report that.
`verdict.indicative` never downgrades `verdict.level`; it qualifies it. Quote both.
A backend that cannot run produces `level: not_executed` with a reason and exit code 3 —
do not re-run it in a loop hoping for a different answer, and never present a
`not_executed` report as a passing one. VM configuration is local and git-ignored
(`configs/vm.local.yaml`, or `$AWT_VM_CONFIG`); no image path, snapshot name or
credential is committed. Protocol: `docs/vm-protocol.md`; VMs worth building:
`docs/reference-vms.md`.

## 5. Iteration rules
- Change one thing at a time; re-run `./run.sh`; compare with the previous report in
  `.sandbox/history/`.
- A call to an unimplemented shim function, or a Windhawk-specific compile error, is a
  probable SHIM bug: consult `docs/official/`, fix the shim, add a regression test in
  `tests/`, re-run. Edit the mod only when the mod itself is wrong.
- Keep `docs/shim-status.md` fresh: `python3 -m awt.conformance`.

## 6. Links
See `docs/links.md` (all URLs verified). Official headers cached in `docs/official/`.

## 7. Safety
Everything stays inside this repository directory: Wine prefix, logs, builds and
storage live in `.sandbox/`. Never write outside it; never touch host config except
the per-prefix registry (CrashHandler) that `setup.sh` sets inside `.sandbox/prefix`.
