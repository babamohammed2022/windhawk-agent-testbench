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
