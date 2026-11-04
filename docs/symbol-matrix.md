# Feature 1 — symbol verification across Windows builds

```bash
python3 -m awt.symbols examples/internal-symbol-demo.wh.cpp
python3 -m awt.symbols mod.wh.cpp --matrix configs/symbol-matrix.yaml --json .sandbox/symbol-matrix.json
python3 -m awt.symbols mod.wh.cpp --list          # what does the mod ask for? no network
python3 -m awt.symbols mod.wh.cpp --offline       # honest 'unknown' everywhere
```

## 1. The question this answers

A mod that hooks `twinui.pcshell.dll` by symbol does nothing at all when the
symbol it asks for is not in the user's build. Under Wine the bench cannot even
ask: there are no Microsoft PDBs, and the shim fails `HookSymbols` on purpose
(`docs/limitations.md`, item 3). So the bench could say *nothing* about
shell mods — the most common class of mod.

This feature answers a narrower question that **can** be answered on Linux, with
no Windows and without executing anything:

> For each internal symbol this mod asks Windows for, and for each Windows build
> in the matrix: does that name exist in the public PDB Microsoft ships for that
> exact binary, and at which RVA?

That is a statement about *names*, not about behaviour. It does not upgrade the
verdict ladder, and it never marks anything as `verified`.

## 2. Outcome vocabulary

| outcome | meaning | what it is **not** |
|---|---|---|
| `found` | the name exists in the public PDB of that exact binary | not proof the hook installs, nor that the signature/behaviour match what the mod assumes |
| `missing` | the PDB was downloaded, parsed and searched; the name is not in it | not "the mod is broken" — the mod may target other builds |
| `ambiguous` | several symbols matched, or two copies at different RVAs, or the only agreement is case-insensitive | not a find: Windhawk compares exact strings and takes the first match |
| `unknown` | the check could not be performed (no network, no signature for that build, module absent from that build, unreadable PDB, module not resolved from the source) | **never** reported as `found`, and never as `missing` |

Every cell carries `confidence`, `basis`, `notes` and (for a find) `rva`,
`matched_name` and the candidate list, so a reader can audit the verdict instead
of trusting it.

`confidence` says how the name comparison was made:

* `exact` — byte-identical string. This is the only test the Windhawk engine
  performs (`src/windhawk/engine/mod.cpp` compares the mod's string with DIA's
  `get_undecoratedNameEx` output, byte for byte).
* `structural` — the decorated PDB name was decoded and its qualified name,
  access specifier, static/virtual and parameter count agree with the
  undecorated text the mod wrote. Evidence the symbol exists; **not** evidence
  the strings are identical.
* `partial` — one side decoded only partially (template arguments, an encoding
  this demangler does not cover), or the name matches while the parameter count
  does not (a different overload: Windhawk would *not* bind the mod's name to
  it). The notes say which.
* `weak` — case-insensitive agreement only, which Windhawk would not match at
  runtime; reported as `ambiguous`.

## 3. Pipeline

```
mod.wh.cpp ──extract.py──▶ symbol requests (name, module, origin, line, optional, alternatives)
                                │
configs/symbol-matrix.yaml ─────┤        module + build id
                                ▼
winbindex.py / pinned table ──▶ PE signature  <TIMESTAMP:08X><SizeOfImage:x>
                                ▼
symserver.py ────────────────▶ PE image  ──pe.py──▶ debug directory (RSDS)
                                ▼                     │ GUID + Age + PDB file name
symserver.py ────────────────▶ PDB ──pdb/{msf,reader,records}.py──▶ symbol list (name, RVA, kind)
                                ▼
match.py + demangle.py ─────▶ found / ambiguous / missing  (+ confidence, notes)
```

Anything that fails along the way produces `unknown` **with the reason**, and the
reason is the message from the step that failed (`PE not available: HTTP 404 …`,
`signature not resolvable: taskbar.dll is not listed for 1507 …`, `PDB
unreadable: …`). No step is allowed to interpret a failure as absence.

### 3.1 What the extractor understands

Ordered by confidence, each request keeps its `origin`:

1. `symbol_hook_table` — `WindhawkUtils::SYMBOL_HOOK` / `WH_SYMBOL_HOOK`
   initialiser lists passed to `HookSymbols`. Per entry: the list of alternative
   names (Windhawk takes the first that matches), the `optional` flag, and the
   module argument of the `HookSymbols` call that consumes *that* table.
2. `find_symbol_compare` — literals compared with `WH_FIND_SYMBOL::symbol` /
   `symbolDecorated` inside a `Wh_FindFirstSymbol` enumeration.
3. `public_api_hook` — `Wh_SetFunctionHook` targets, including the common
   `p = GetProcAddress(GetModuleHandle(L"m.dll"), "Name")` indirection. A hook
   target this scan cannot trace is reported in `unresolved_hooks` with a reason
   rather than guessed at.
4. `decorated_literal` — any other literal shaped like a symbol name.

Real mods write one name as several adjacent literals, often with a macro in the
middle and an `#ifdef _WIN64` around it:

```cpp
{ L"public: virtual long " THISCALL_STR L" CNscTree::AppendRoot(" L"struct IShellItem *,…)" }
```

so the extractor resolves preprocessor conditionals first (keeping the x86-64
branch, blanking dropped lines with spaces so every line number stays real),
expands object-like macros whose body is a single literal, and then performs C++
adjacent-literal concatenation. A condition it cannot evaluate — a mod-specific
`#ifdef ENABLE_X` — keeps **both** branches and records a note: an extra
candidate with a note is safer than a silently dropped symbol.

`module` is resolved with its `module_basis`: `variable` (an `HMODULE` variable
traced to a `LoadLibrary`/`GetModuleHandle` literal), `inline-literal`,
`api-table` (a documented export → module map), `explicit`,
`getprocaddress-variable`, `include-metadata`, `inferred-single-module`,
`process-include`, or `unresolved` / `none`. A basis is always reported, because
"inferred" and "read from the source" are not the same claim.

### 3.2 PDB analysis: which tool, and why

`awt/pdb` is a **pure-Python MSF 7.0 reader** (container → PDB info stream → DBI
→ section headers → public/global symbol streams). Alternatives evaluated:

* `llvm-pdbutil` — the most faithful reader, but not installed in the sandboxes
  this bench runs in and not installable without package network access. Used as
  an *optional* cross-check only: `--cross-check` runs it when present and
  records the comparison in the report.
* `pdbparse` — unmaintained, does not decode the DBI/public-symbol streams of
  modern public PDBs, and pulls in `construct`. Rejected.

The reader only needs what the question needs: names, kinds and RVAs. It is
defensive by construction — the `S_GPROC32` record layout is documented both with
and without a `typind` field, so both are tried and the one that yields coherent
segments wins (the choice is reported per record area); the GSI record area is
located by validating that a record walk ends exactly at the stream end. A stream
that cannot be parsed produces a warning and `unknown`, never `missing`.

`awt/pe.py` is a matching pure-Python PE/COFF reader: enough to read the debug
directory and turn the RSDS record into the PDB's symbol-store key.

### 3.3 Name comparison

`demangle.py` decodes MSVC decorated names for x86 and x86-64: access/convention
codes, pointer and reference chains with `__ptr64` and cv-qualifiers, class /
struct / union / enum and template names, type back-references, function-pointer
types, data symbols, and the special names (`??0` constructor, `??1` destructor,
`??_E` / `??_G` deleting destructors, `??_7` vftable, operators). Details that
matter and are easy to get wrong:

* MSVC writes qualified names **innermost first** (`?ShowStartView@XamlLauncher@@`
  is `XamlLauncher::ShowStartView`);
* `XZ` is an **empty** parameter list, which DIA prints as `f(void)`;
* static member functions and global functions **do** carry a return type,
  constructors and destructors do not;
* `D` is the code for `char`, not a cv-qualifier: `PEAD` is `char *__ptr64` and
  `PEBD` is `const char *__ptr64`;
* the split between convention modifiers and return type is ambiguous from text
  alone, so candidate strips are scored by how completely the type walk consumes
  the tail, instead of taking the first that parses.

When `llvm-undname` is installed it is preferred (`--llvm-undname`) because it
reproduces DIA's undecorated text exactly, which turns `structural` into `exact`.
Without it, the built-in decoder is used and every verdict says so.

Windhawk performs **no** wildcard matching. A request that contains `*` or `?` as
a pattern is still answered with `fnmatch`, but the notes mark it *indicative*.

### 3.4 Where the binaries come from

The Microsoft public symbol server serves a file only when its signature is
known; it has no directory listing. Signatures come from either:

* the **pinned table** in `configs/symbol-matrix.yaml` (values read out of
  winbindex on 2026-11-04; they make a run reproducible and let the tool work
  when `api.github.com` is unreachable), or
* a **live winbindex lookup** (`m417z/winbindex@gh-pages`,
  `data/by_filename_compressed/<module>.json.gz` through `api.github.com`),
  picking the newest amd64 entry listed for the requested channel.

Only public *metadata* (version string, PE timestamp and size) is stored in this
repository. Downloaded PEs and PDBs go to `.cache/symbols/` (git-ignored) and are
**never committed**. Parsed symbol lists are cached as JSON under
`.cache/symbols/index/`, so a second run needs no network at all.

## 4. The build matrix

`configs/symbol-matrix.yaml` lists the builds, the symbol server, the winbindex
settings, the pinned signatures and the limits. The committed default covers the
six required builds — Windows 10 1507, Windows 10 22H2, Windows 11 21H2, 23H2,
24H2 and 25H2 — plus one disabled example entry. Machine-specific overrides go
in `configs/symbol-matrix.local.yaml` (git-ignored) or `--matrix <path>` /
`$AWT_SYMBOL_MATRIX`.

Two real facts are encoded in the pinned table and produce `unknown`, not
`missing`: `twinui.pcshell.dll` does not exist before Windows 10 1607, and
`taskbar.dll` does not exist on Windows 10 at all.

Refresh the pinned values from the live index with:

```bash
python3 -m awt.symbols --refresh-pinned twinui.pcshell.dll,explorer.exe,taskbar.dll
```

## 5. Report output

`--json <path>` writes a standalone document (`schema: 1`, `kind:
"symbol_matrix"`, plus `environment`) whose `symbol_matrix` section contains:
`disclaimer`, `matrix` (what was configured), `extraction` (counts by origin,
kind and module basis, preprocessor notes, assumed defines), `modules` (per
module and build: availability, PE and PDB identity, fetch notes, symbol count,
reader warnings, reason when unavailable), `symbols` (one row per request with a
cell per build), `summary` (row outcomes, per-build counts, how many missing
symbols the mod marks optional), `network` (server config, reachability probe,
download statistics, winbindex notes), `tooling` and `seconds`.

The same section can be attached to a normal bench report without changing any
existing key:

```bash
python3 -m awt.symbols mod.wh.cpp --json .sandbox/symbol-matrix.json
python3 -m awt.cli run mod.wh.cpp --symbols-json .sandbox/symbol-matrix.json
```

`report.attach_symbol_matrix()` adds `symbol_matrix` and one extra
`verdict.symbol_matrix_note`; it never changes `verdict.level`, because a name
existing in a PDB is not an execution. A report with no matrix run simply has no
`symbol_matrix` key, so older consumers keep working.

## 6. What was actually executed

Run on Debian 12, Python 3.11, in a sandbox with **no** Wine, no MinGW and no
access to `msdl.microsoft.com` (TLS to that host is blocked here):

* `tests/unit/run_unit.py` — 117 tests pass under a venv with the optional
  accelerators (PyYAML, numpy, Pillow), 115 pass with the dependency-free system
  interpreter (the two PyYAML cross-checks skip). `tests/run_tests.sh` exits 2:
  stage 1 passes, the Wine acceptance stage is honestly skipped.
* The **whole pipeline** runs end to end in `tests/unit/test_symbols_verify.py`
  against an HTTP server on `127.0.0.1` that serves synthetic PE/PDB images:
  signature → PE download → debug directory → PDB download by GUID+Age → MSF/DBI
  parse → match. It asserts `found` (exact), `found` (structural), `missing`,
  `unknown` for an offline run, for an unreachable server, for a module absent
  from a build and for a PE without a debug directory, plus cache reuse with the
  server switched off and the CLI exit codes (0 resolved, 3 nothing resolved,
  2 usage error).
* The extractor and the demangler were validated against the **860 published
  mods** in `ramensoftware/windhawk-mods` (commit
  `de69b332c19effb929b5217281f61044930001e7`, cloned into the git-ignored
  `.sandbox/ref/`): 3824 symbol requests extracted — 2052 from `SYMBOL_HOOK`
  tables, 1323 public-API hooks, 449 decorated literals; all 302 mods that
  declare a hook table yield at least one request; a target module was resolved
  for 97.4 % of requests. Of the 375 distinct decorated names, 357 decode with
  confidence `structural` and 18 with `partial` (Chromium-style encodings:
  `__Cr@std`, `_SU`, `_N2@Z`). Nothing from that repository is committed;
  `tests/unit/fixtures/real_symbols.py` keeps only short compiler-generated names
  with their provenance.

**Not executed:** no byte was ever downloaded from `msdl.microsoft.com` in this
sandbox, so no verdict in this document is based on a real Microsoft PDB, and no
mod was compiled or run. The real-network path is implemented and unit-tested
against a local server; the first run on a machine with symbol-server access is
the first real evidence.

## 7. Known limits

* The demangler is partial by design. When it cannot decode a construct it says
  so (`confidence: partial`) and compares the qualified name only. Template
  *arguments* are never compared — that needs a full demangler.
* DIA's exact undecorated text is only reproducible with `llvm-undname`; without
  it a `found` can be `structural` rather than `exact`, which is weaker evidence
  than what the engine does at runtime.
* A public PDB may lack the global symbol stream; the reader warns and the
  affected lookups stay `unknown`.
* Static analysis of the mod source cannot see names built at runtime
  (`std::wstring` concatenation, tables filled in a loop). 302 of 302 published
  mods were covered, but that is an observation about those mods, not a guarantee.
* Symbols are checked per **module file name**, not per module version the user
  actually has installed: a build id maps to one specific binary through winbindex
  or the pinned table, and a machine with a different cumulative update can
  differ. The report records exactly which binary was used (version, timestamp,
  size, PDB GUID).
* An RVA is a fact about that binary; it says nothing about calling conventions,
  struct layouts or whether hooking the function is safe.
