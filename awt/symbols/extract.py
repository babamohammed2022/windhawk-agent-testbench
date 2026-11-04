"""Extract the symbols a mod asks Windows for, from its source text.

Nothing here needs to compile the mod: this is a careful C++ *scan*, and every
extracted request keeps its origin (API used, line number, literal text) so the
report can show why a symbol is being looked up.

Sources of symbol names, in order of confidence:

1. `symbol_hook_table` -- `WindhawkUtils::SYMBOL_HOOK` / `WH_SYMBOL_HOOK`
   initialiser lists passed to `HookSymbols`. Each entry contributes a list of
   alternative names (Windhawk uses the first that matches) plus the `optional`
   flag of the entry.
2. `find_symbol_compare` -- literals compared against `WH_FIND_SYMBOL::symbol` /
   `symbolDecorated` inside a `Wh_FindFirstSymbol` enumeration loop.
3. `decorated_literal` -- any raw/wide literal that is shaped like an MSVC
   decorated name (`?...@@...`) or an undecorated signature
   (`public: virtual long __cdecl A::B(...)`).
4. `public_api_hook` -- the target of `Wh_SetFunctionHook` /
   `WindhawkUtils::SetFunctionHook<T>`; the module is inferred from the
   API->DLL table, which is what Windows itself does at link time.

Target modules come from the mod metadata (`@include`), from
`GetModuleHandle*`/`LoadLibrary*` literals, and from a variable->module map used
to resolve the first argument of `HookSymbols`.
"""
import os
import re

from .. import metadata as metadata_mod

__all__ = ["SymbolRequest", "extract_requests", "extract_modules", "API_MODULE_MAP",
           "scan"]

# Public Win32 APIs a mod can hook with Wh_SetFunctionHook, and the module that
# exports them. Kept small and explicit: an API that is not listed yields a
# request with module=None ("unknown module"), never a wrong guess.
API_MODULE_MAP = {
    "CreateFileW": "kernel32.dll", "CreateFileA": "kernel32.dll",
    "ReadFile": "kernel32.dll", "WriteFile": "kernel32.dll",
    "CloseHandle": "kernel32.dll", "GetProcAddress": "kernel32.dll",
    "LoadLibraryW": "kernel32.dll", "LoadLibraryExW": "kernel32.dll",
    "GetModuleHandleW": "kernel32.dll", "GetTickCount": "kernel32.dll",
    "SetThreadExecutionState": "kernel32.dll",
    "SendMessageW": "user32.dll", "SendMessageTimeoutW": "user32.dll",
    "PostMessageW": "user32.dll", "SetWindowPos": "user32.dll",
    "ShowWindow": "user32.dll", "CreateWindowExW": "user32.dll",
    "RegisterClassExW": "user32.dll", "SetWindowsHookExW": "user32.dll",
    "CallNextHookEx": "user32.dll", "GetWindowRect": "user32.dll",
    "SetWindowRgn": "user32.dll", "DrawTextW": "user32.dll",
    "SystemParametersInfoW": "user32.dll", "TrackPopupMenu": "user32.dll",
    "TrackPopupMenuEx": "user32.dll", "GetDC": "user32.dll",
    "Shell_NotifyIconW": "shell32.dll", "SHGetFileInfoW": "shell32.dll",
    "SHGetPropertyStoreFromParsingName": "shell32.dll",
    "ImageList_Create": "comctl32.dll", "ImageList_Add": "comctl32.dll",
    "RegOpenKeyExW": "advapi32.dll", "RegQueryValueExW": "advapi32.dll",
    "RegSetValueExW": "advapi32.dll",
    "DwmExtendFrameIntoClientArea": "dwmapi.dll",
    "DwmSetWindowAttribute": "dwmapi.dll", "DwmEnableBlurBehindWindow": "dwmapi.dll",
    "GetSystemMetrics": "user32.dll", "CreateFontW": "gdi32.dll",
    "SetBkMode": "gdi32.dll", "SelectObject": "gdi32.dll",
    "PathFileExistsW": "shlwapi.dll", "StrStrIW": "shlwapi.dll",
    "CoCreateInstance": "ole32.dll",
}

# Defines assumed when resolving the mod's preprocessor conditionals. This
# bench builds x86-64 only (docs/limitations.md item 8), so _WIN64 is defined
# and _M_IX86 is not: that is what decides which symbol-name spelling a mod
# compiles, and therefore which name must exist in the PDB.
DEFAULT_DEFINES = frozenset({
    "_WIN64", "_WIN32", "WIN32", "_WINDOWS", "__x86_64__", "_M_X64",
    "UNICODE", "_UNICODE", "WH_MOD", "WH_EDITING", "_MSC_VER", "NDEBUG",
})

# Macros that are definitely *not* defined when Windhawk compiles an x86-64 mod.
# Anything outside DEFAULT_DEFINES and this set is a mod-specific or build-system
# macro whose value we cannot know, and a condition that uses it is unevaluable:
# both branches are kept, because silently dropping one would hide symbols.
KNOWN_UNDEFINED = frozenset({
    "_M_IX86", "_X86_", "__i386__", "_M_IA64", "_M_ARM", "_M_ARM64",
    "__aarch64__", "_M_ARM64EC", "_M_HYBRID", "__clang__", "__GNUC__",
})

PREPROC_IF_RE = re.compile(r"^\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)$")
IDENT_RE = re.compile(r"[A-Za-z_]\w*")
CONDITION_WORDS = {"defined", "true", "false", "and", "or", "not"}


def unknown_macros(expr: str, defines) -> set:
    """Identifiers in a preprocessor condition whose value we cannot know."""
    names = set(IDENT_RE.findall(expr or "")) - CONDITION_WORDS
    return names - set(defines) - KNOWN_UNDEFINED


def _evaluate_condition(expr: str, defines) -> bool:
    """Evaluate a restricted preprocessor expression; None when not understood."""
    text = expr.strip()
    text = re.sub(r"defined\s*\(\s*([A-Za-z_]\w*)\s*\)",
                  lambda m: "(%d)" % (1 if m.group(1) in defines else 0), text)
    text = re.sub(r"defined\s+([A-Za-z_]\w*)",
                  lambda m: "(%d)" % (1 if m.group(1) in defines else 0), text)
    text = text.replace("&&", " and ").replace("||", " or ")
    text = re.sub(r"!(?!=)", " not ", text)
    # only identifiers that are defines (treated as 1) or numbers/operators left
    if not re.fullmatch(r"[0-9A-Za-z_ \t()<>=!+\-*/andort]+", text):
        return None
    names = set(re.findall(r"[A-Za-z_]\w*", text)) - {"and", "or", "not"}
    env = {n: (1 if n in defines else 0) for n in names}
    try:
        return bool(eval(text, {"__builtins__": {}}, env))  # noqa: S307
    except Exception:
        return None


def resolve_preprocessor(source: str, defines=DEFAULT_DEFINES):
    """Keep the lines the x86-64 build would compile; blank the others.

    Line numbers and column offsets are preserved (dropped text becomes spaces)
    so every extracted symbol still points at its real line. A condition that
    cannot be evaluated keeps *both* branches and is reported in `notes`: for
    symbol extraction an extra candidate with a note is safer than silently
    dropping one.
    """
    lines = source.split("\n")
    out = list(lines)
    notes = []
    # stack entries: (currently_active, any_branch_taken, parent_active)
    stack = []
    for idx, line in enumerate(lines):
        m = PREPROC_IF_RE.match(line)
        if not m:
            if stack and not stack[-1][0]:
                out[idx] = " " * len(line)
            continue
        directive, expr = m.group(1), m.group(2)
        parent_active = stack[-1][0] if stack else True
        unknown = bool(unknown_macros(expr, defines)) if directive in (
            "if", "elif", "ifdef", "ifndef") else False
        if directive == "ifdef":
            name = expr.strip().split()[0] if expr.strip() else ""
            cond = None if unknown_macros(expr, defines) else (name in defines)
        elif directive == "ifndef":
            name = expr.strip().split()[0] if expr.strip() else ""
            cond = None if unknown_macros(expr, defines) else (name not in defines)
        elif directive in ("if", "elif"):
            cond = _evaluate_condition(expr, defines)
            if cond is None and not unknown:
                unknown = True
        if cond is None and directive in ("if", "ifdef", "ifndef", "elif"):
            notes.append("line %d: cannot evaluate '#%s %s' (%s); both branches "
                         "kept, so a symbol may be listed that this build does not "
                         "compile" % (idx + 1, directive, expr.strip()[:60],
                                      ", ".join(sorted(unknown_macros(expr, defines)))
                                      or "expression not understood"))
            cond = True
        elif directive == "else":
            cond = None          # handled below
        elif directive == "endif":
            if stack:
                stack.pop()
            continue
        if directive in ("if", "ifdef", "ifndef"):
            active = bool(cond) if parent_active else False
            stack.append([active, bool(cond), parent_active, unknown])
        elif directive == "elif":
            if stack:
                top = stack[-1]
                top[3] = top[3] or unknown
                active = parent_active and (top[3] or (not top[1] and bool(cond)))
                top[0] = active
                top[1] = top[1] or bool(cond)
        elif directive == "else":
            if stack:
                top = stack[-1]
                top[0] = top[2] and (top[3] or not top[1])
                top[1] = True
        if stack and not stack[-1][0]:
            out[idx] = " " * len(line)
    for i, line in enumerate(lines):
        if PREPROC_IF_RE.match(line):
            out[i] = " " * len(line)
    return "\n".join(out), notes


DEFINE_RE = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)(?:\([^)]*\))?\s*(.*)$")


def collect_macro_literals(source: str, depth: int = 3) -> dict:
    """Object-like macros whose body is a single string literal.

    Real mods put the calling convention in a macro
    (`#define THISCALL_STR L"__cdecl"`) and splice it into a symbol name built
    out of adjacent literals. Recognising those macros keeps the name whole;
    anything more complex is left alone (the parts are then reported separately).
    """
    macros = {}
    for line in source.split("\n"):
        m = DEFINE_RE.match(line)
        if not m:
            continue
        name, body = m.group(1), m.group(2).strip()
        lit = re.fullmatch(r'(?:u8|u|U|L)?"((?:[^"\\]|\\.)*)"', body)
        if lit:
            macros[name] = lit.group(1)
    for _ in range(depth):          # resolve macros defined in terms of macros
        changed = False
        for name, body in list(macros.items()):
            if body in macros and body != name:
                macros[name] = macros[body]
                changed = True
        if not changed:
            break
    return macros


def merge_adjacent_literals(literals, code: str, macros: dict = None) -> list:
    """Join string literals separated only by whitespace (C++ concatenation).

    Real mods build one symbol name out of several literals, often with an
    `#ifdef _WIN64` between them; after `resolve_preprocessor` those parts sit
    next to each other and mean a single name.
    """
    macros = macros or {}
    merged = []
    for entry in literals:
        start, end, kind, text, line = entry
        if merged:
            prev_start, prev_end, prev_kind, prev_text, prev_line = merged[-1]
            between = code[prev_end:start].strip()
            if prev_kind != "char" and kind != "char":
                if between == "":
                    merged[-1] = (prev_start, end, kind, prev_text + text, prev_line)
                    continue
                if between in macros:
                    merged[-1] = (prev_start, end, kind,
                                  prev_text + macros[between] + text, prev_line)
                    continue
        merged.append(entry)
    return merged


DECORATED_RE = re.compile(r"^\?[A-Za-z0-9_$@?]*@@")
UNDECORATED_RE = re.compile(
    r"^(?:(?:public|private|protected)\s*:)?\s*"
    r"(?:(?:static|virtual|abstract)\s+)*"
    r".*?__(?:cdecl|stdcall|fastcall|vectorcall|thiscall|clrcall)\s+"
    # a qualified name, optionally with one or more template argument lists
    # (`winrt::impl::produce<A,B>::MeasureOverride(`), then the parameter list.
    r"[\w:]+(?:[\w:]|<[^;]*?>)*\s*\(")
PLAIN_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# 32-bit stdcall/fastcall decorations (`_CKbd_GetDeviceState@8`, `@fn@12`).
# They are complete names: no decoding is needed, they are compared byte for byte.
X86_STDCALL_RE = re.compile(r"^_[A-Za-z_$][A-Za-z0-9_$]*@\d{1,4}$")
X86_FASTCALL_RE = re.compile(r"^@[A-Za-z_$][A-Za-z0-9_$]*@\d{1,4}$")

STRING_PREFIX_RE = re.compile(r"(?:u8|u|U|L)?R?$")


class SymbolRequest(dict):
    """One symbol the mod wants, with everything needed to audit the lookup."""

    __getattr__ = dict.get

    def __repr__(self):  # pragma: no cover
        return "SymbolRequest(%r, module=%r, origin=%r)" % (
            self.get("symbol"), self.get("module"), self.get("origin"))


def scan(source: str):
    """Split C++ source into (masked_code, literals).

    `masked_code` has comments and the *contents* of string/char literals
    replaced by spaces, so braces, identifiers and call structure survive while
    literal text cannot confuse it. `literals` records each literal as
    (start, end, kind, text, line) with kind in {"raw", "wide", "narrow", "char"}.
    Line numbers stay aligned because every masked character is a space and new
    lines are preserved.
    """
    out = []
    literals = []
    i, n = 0, len(source)
    line = 1
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if ch == "\n":
            out.append(ch)
            line += 1
            i += 1
            continue
        if ch == "/" and nxt == "/":
            while i < n and source[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if ch == "/" and nxt == "*":
            out.append("  ")
            i += 2
            while i < n and not (source[i] == "*" and i + 1 < n and source[i + 1] == "/"):
                out.append("\n" if source[i] == "\n" else " ")
                if source[i] == "\n":
                    line += 1
                i += 1
            if i < n:
                out.append("  ")
                i += 2
            continue
        prefix = _raw_prefix(source, i)
        if prefix is not None:
            start = i
            delim, i = _read_raw_string(source, i + len(prefix), line)
            if delim is None:
                out.append(" " * (i - start))
                continue
            text, end, lines = delim
            literals.append((start, end, "raw", text, line))
            masked = source[start:end]
            out.append(_mask(masked, lines))
            line += lines
            i = end
            continue
        if ch in "\"'":
            kind = "wide" if (i and source[i - 1] in "LuU" and
                              STRING_PREFIX_RE.match(source[max(0, i - 1):i])) else \
                ("char" if ch == "'" else "narrow")
            # include the L/u/U/u8 prefix in the literal span: adjacent-literal
            # concatenation has to see only whitespace between two parts.
            start = i
            j = i
            while j > 0 and source[j - 1] in "LuU8R":
                prev = source[j - 2] if j >= 2 else ""
                if prev.isalnum() or prev == "_":
                    break
                j -= 1
            start = j
            quote = ch
            i += 1
            text = []
            while i < n:
                c = source[i]
                if c == "\\" and i + 1 < n:
                    text.append(source[i:i + 2])
                    if source[i + 1] == "\n":
                        line += 1
                    i += 2
                    continue
                if c == quote:
                    i += 1
                    break
                if c == "\n":
                    break  # unterminated; stop rather than swallow the file
                text.append(c)
                i += 1
            body = "".join(text)
            literals.append((start, i, kind, body, line))
            if start < i and start != i - 1:
                # the prefix characters were already emitted as code: drop them
                # so the masked text stays aligned with the source offsets.
                prefix_len = i - start
                while prefix_len and out and out[-1] in "LuU8R":
                    out.pop()
                    prefix_len -= 1
            out.append(_mask(source[start:i], body.count("\n")))
            continue
        out.append(ch)
        i += 1
    return "".join(out), literals


def _mask(text: str, newlines: int) -> str:
    masked = []
    for ch in text:
        masked.append("\n" if ch == "\n" else " ")
    return "".join(masked)


def _raw_prefix(source: str, i: int):
    """Length-consuming detection of a raw string literal prefix at `i`."""
    for prefix in ("u8R\"", "uR\"", "UR\"", "LR\"", "R\""):
        if source.startswith(prefix, i):
            return prefix
    return None


def _read_raw_string(source: str, after_prefix: int, line: int):
    """Parse `delim(...)delim"` starting right after the `R"` prefix."""
    n = len(source)
    j = after_prefix
    delim_start = j
    while j < n and source[j] not in "()\n" and (j - delim_start) < 16:
        j += 1
    if j >= n or source[j] != "(":
        return None, after_prefix
    delim = source[delim_start:j]
    terminator = ")" + delim + "\""
    end = source.find(terminator, j + 1)
    if end == -1:
        return None, n
    text = source[j + 1:end]
    total_end = end + len(terminator)
    return (text, total_end, source.count("\n", after_prefix, total_end)), total_end


def _line_of(source: str, pos: int) -> int:
    return source.count("\n", 0, pos) + 1


def _literal_at(literals, pos, tolerance=0):
    for start, end, kind, text, line in literals:
        if start - tolerance <= pos < end + tolerance:
            return (start, end, kind, text, line)
    return None


def _literal_following(literals, pos, max_gap=8):
    """First literal that starts within `max_gap` characters after `pos`."""
    best = None
    for entry in literals:
        if pos <= entry[0] <= pos + max_gap:
            if best is None or entry[0] < best[0]:
                best = entry
    return best


def _balanced(text: str, start: int, open_ch="{", close_ch="}") -> int:
    """Index just after the block opened at `start`, or -1."""
    if start >= len(text) or text[start] != open_ch:
        return -1
    depth = 0
    for i in range(start, len(text)):
        if text[i] == open_ch:
            depth += 1
        elif text[i] == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def _split_top_level_spans(text: str, base: int = 0) -> list:
    """Spans of the *entries* of a brace-initialised table.

    `text` is the whole initialiser including its outer braces, so an entry is a
    child at depth 2, not depth 1: getting this wrong merges the entire table
    into one entry and every symbol then inherits the other symbols as
    "alternative names", which silently turns a miss into a match.
    Returns [(abs_start, abs_end, text)].
    """
    spans, depth, start, i, in_str, opened = [], 0, None, 0, False, False
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
        elif ch in "([{":
            depth += 1
            if depth == 1:
                opened = True          # the table itself: not an entry
            elif depth == 2 and start is None:
                start = i
        elif ch in ")]}":
            if depth == 2 and start is not None:
                spans.append((base + start, base + i + 1, text[start:i + 1]))
                start = None
            depth -= 1
        i += 1
    if start is not None:              # unterminated entry: report what we have
        spans.append((base + start, base + len(text), text[start:]))
    if not spans and opened:
        # single-hook form: `SYMBOL_HOOK hook = { {names}, &orig, fn, false };`
        # the outer brace *is* the entry.
        spans.append((base, base + len(text), text))
    return spans


def _split_top_level(text: str, open_ch="{", close_ch="}") -> list:
    """Split `{a, b}, {c}` into the outer elements, ignoring nested commas."""
    parts, depth, cur, in_str = [], 0, [], False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_str:
            cur.append(ch)
            if ch == "\\":
                if i + 1 < len(text):
                    cur.append(text[i + 1])
                    i += 2
                    continue
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            cur.append(ch)
        elif ch in "([{":
            depth += 1
            cur.append(ch)
        elif ch in ")]}":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


def extract_modules(code: str, literals, meta: dict) -> dict:
    """Resolve the target modules: metadata includes + literal-based lookups."""
    modules = {
        "includes": [i for i in meta.get("include", []) if i != "*"],
        "loaded": [],
        "variable_map": {},
        "process_module": None,
    }
    modules["proc_address_map"] = {}
    load_re = re.compile(r"(GetModuleHandle[WA]?|LoadLibrary(?:Ex)?[WA]?|GetModuleHandleW)\s*\(")
    for m in load_re.finditer(code):
        lit = _literal_following(literals, m.end(), max_gap=6)
        if lit and lit[3]:
            name = lit[3].strip()
            if name and name.lower() not in ("null", "nullptr"):
                entry = {"module": name, "api": m.group(1), "line": lit[4]}
                modules["loaded"].append(entry)
                # HMODULE var = LoadLibraryW(L"x.dll")
                assign = re.search(r"([A-Za-z_]\w*)\s*=\s*$", code[:m.start()][-80:])
                if assign:
                    modules["variable_map"][assign.group(1)] = name

    # `auto p = (void*)GetProcAddress(GetModuleHandle(L"dinput8.dll"), "Name");`
    # followed by `Wh_SetFunctionHook(p, ...)`: the interesting symbol is the
    # export name and the module comes from the nested handle call.
    for m in re.finditer(r"([A-Za-z_]\w*)\s*=\s*(?:\([^)]{0,60}\)\s*)*"
                         r"GetProcAddress\s*\(", code):
        var = m.group(1)
        start = m.end() - 1
        end = _balanced(code, start, "(", ")")
        if end == -1:
            continue
        args = _split_top_level(code[start + 1:end - 1])
        if not args:
            continue
        # the export name is a literal, so in the masked code the second argument
        # is blank: take the first literal that follows the handle expression.
        lit = _literal_following(literals, start + len(args[0]) + 1, max_gap=200)
        api_name = lit[3].strip() if lit else None
        handle = args[0].strip()
        module = None
        nested = load_re.search(handle)
        if nested:
            nlit = _literal_following(literals, start + 1 + nested.end(), max_gap=8)
            if nlit:
                module = nlit[3].strip()
        if not module:
            ident = re.match(r"([A-Za-z_]\w*)", handle)
            if ident:
                module = modules["variable_map"].get(ident.group(1))
        if api_name:
            modules["proc_address_map"][var] = {
                "api": api_name, "module": module,
                "line": lit[4] if lit else _line_of(code, m.start()),
                "handle": handle,
            }
    includes = modules["includes"]
    modules["include_modules"] = list(includes)
    # `@target` is the process the mod is injected into; `@include` are the
    # modules whose internal symbols it hooks. Conflating them made a hook table
    # for one DLL inherit another DLL as its module.
    target = (meta.get("raw") or {}).get("target")
    modules["process_module"] = target or None
    # de-duplicate loaded modules, keep first occurrence
    seen, uniq = set(), []
    for entry in modules["loaded"]:
        key = (entry["module"], entry["api"])
        if key not in seen:
            seen.add(key)
            uniq.append(entry)
    modules["loaded"] = uniq
    return modules


def _candidate_is_symbol(text: str) -> str:
    """Classify a literal: 'decorated', 'undecorated', 'plain' or None."""
    text = text.strip()
    if not text or len(text) > 400:
        return None
    if DECORATED_RE.match(text):
        return "decorated"
    if UNDECORATED_RE.match(text):
        return "undecorated"
    if X86_STDCALL_RE.match(text) or X86_FASTCALL_RE.match(text):
        return "decorated"
    if PLAIN_SYMBOL_RE.match(text) and not text.startswith("__"):
        return "plain"
    return None


def extract_requests(source: str, path: str = "<source>", defines=DEFAULT_DEFINES) -> dict:
    """All symbol requests of a mod, grouped by origin, plus module resolution."""
    resolved, preproc_notes = resolve_preprocessor(source)
    meta = metadata_mod.parse_metadata(resolved)
    code, literals = scan(resolved)
    macros = collect_macro_literals(resolved)
    literals = merge_adjacent_literals(literals, code, macros)
    modules = extract_modules(code, literals, meta)
    requests = []

    def add(symbol, origin, line, module=None, optional=False, api=None,
            alternatives=None, note=None, kind=None, module_basis=None):
        requests.append(SymbolRequest(
            symbol=symbol, module=module, module_basis=module_basis or
            ("api-table" if origin == "public_api_hook" and module else
             ("process-include" if module else "explicit")),
            origin=origin, line=line,
            optional=bool(optional), api=api or origin,
            alternatives=alternatives or [symbol], note=note,
            kind=kind or _candidate_is_symbol(symbol) or "unknown",
            source_file=os.path.basename(path)))

    # ---- 1. SYMBOL_HOOK tables passed to HookSymbols ------------------------
    covered = []          # source ranges already explained by a hook table
    for m in re.finditer(r"(?:WindhawkUtils::)?(WH_)?SYMBOL_HOOK\b", code):
        decl_end = code.find("=", m.end())
        if decl_end == -1 or decl_end - m.end() > 200:
            continue
        brace = code.find("{", decl_end)
        if brace == -1:
            continue
        end = _balanced(code, brace)
        if end == -1:
            continue
        covered.append((brace, end))
        var_match = re.search(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*=$",
                              code[m.end():decl_end + 1])
        table_name = var_match.group(1) if var_match else None
        for span_start, span_end, entry_text in _split_top_level_spans(code[brace:end], brace):
            names = []
            for lit_start, lit_end, kind, text, line in literals:
                if span_start <= lit_start < span_end and kind != "char":
                    value = text.strip()
                    if _candidate_is_symbol(value):
                        names.append((value, line))
            if not names:
                # `{ DRAW_IMMERSIVE_MENU }`: the whole name list is one macro
                # whose body is a single literal.
                for macro_name, macro_text in macros.items():
                    if re.search(r"\b%s\b" % re.escape(macro_name), entry_text) \
                            and _candidate_is_symbol(macro_text.strip()):
                        names.append((macro_text.strip(),
                                      code.count("\n", 0, span_start) + 1))
            if not names:
                continue
            optional = bool(re.search(
                r"(?:,\s*|\b)\s*(?:/\*\s*optional\s*\*/\s*)?true\s*[,}]?\s*$",
                entry_text.strip()))
            for value, line in names:
                add(value, "symbol_hook_table", line, optional=optional,
                    api="WindhawkUtils::HookSymbols",
                    alternatives=[v for v, _ in names],
                    note=("table %s: %d alternative name(s), first match wins"
                          % (table_name or "?", len(names))))
        module, basis = _module_for_hook_symbols(code, literals, table_name, modules)
        for req in requests:
            if req["origin"] == "symbol_hook_table" and req["module"] is None:
                req["module"] = module
                req["module_basis"] = basis

    # ---- 2. literals compared against WH_FIND_SYMBOL fields ----------------
    for m in re.finditer(r"(?:_?wcs(?:icmp|cmp|ncmp|nicmp)|CompareStringW|std::wcs(?:cmp|equal))"
                         r"\s*\([^;]{0,120}?\.symbol(?:Decorated)?\b", code):
        lit = _literal_following(literals, m.start(), max_gap=200)
        if lit and _candidate_is_symbol(lit[3]):
            add(lit[3].strip(), "find_symbol_compare", lit[4],
                api="Wh_FindFirstSymbol",
                note="literal compared with WH_FIND_SYMBOL::symbol in an enumeration")
    for m in re.finditer(r"\.symbol(?:Decorated)?\s*==", code):
        lit = _literal_following(literals, m.end(), max_gap=40)
        if lit and _candidate_is_symbol(lit[3]):
            add(lit[3].strip(), "find_symbol_compare", lit[4],
                api="Wh_FindFirstSymbol", note="equality test on WH_FIND_SYMBOL::symbol")

    # ---- 3. public API hooks ------------------------------------------------
    unresolved_hooks = []
    for m in re.finditer(r"(?:WindhawkUtils::)?(?:Wh_)?SetFunctionHook(?:<[^>]{0,120}>)?\s*\(",
                         code):
        arg = code[m.end():m.end() + 160]
        call_match = re.match(r"\s*(?:\([^)]{0,60}\)\s*)*"
                              r"([A-Za-z_][\w:]*)\s*\(", arg)
        if call_match:
            # Wh_SetFunctionHook(SomeResolver(), ...): the address is computed, so
            # there is no name to look up. Saying so beats guessing one.
            unresolved_hooks.append({
                "line": _line_of(source, m.end()),
                "expression": call_match.group(1) + "()",
                "reason": ("the hooked address is the result of a call (%s) that "
                           "this scan does not evaluate" % call_match.group(1))})
            continue
        name_match = re.match(r"\s*(?:\([^)]{0,60}\)\s*)*&?\s*([A-Za-z_][\w:]*)", arg)
        if not name_match:
            continue
        name = name_match.group(1)
        if name in ("target", "targetFunction", "nullptr", "NULL", "pfn"):
            continue
        resolved = modules.get("proc_address_map", {}).get(name)
        if resolved:
            # Wh_SetFunctionHook(p, ...) with p = GetProcAddress(hMod, "Name")
            add(resolved["api"], "public_api_hook", resolved["line"],
                module=resolved["module"], api="Wh_SetFunctionHook",
                kind="plain", module_basis=("getprocaddress-variable"
                                            if resolved["module"] else "unresolved"),
                note=("export of %s, obtained through GetProcAddress(%s, ...)"
                      % (resolved["module"] or "an unresolved module",
                         resolved["handle"][:40])))
            continue
        if name.islower() and name not in API_MODULE_MAP and "_" not in name:
            # a bare local variable we cannot resolve: report it, do not guess
            unresolved_hooks.append({
                "line": _line_of(source, m.end()), "expression": name,
                "reason": ("the hooked address comes from the variable %r, whose "
                           "value this scan could not trace back to an export"
                           % name)})
            continue
        module = API_MODULE_MAP.get(name)
        add(name, "public_api_hook", _line_of(source, m.end()), module=module,
            api="Wh_SetFunctionHook", kind="plain",
            note=("export of %s" % module) if module else
                 "module not inferred: the API is not in awt.symbols.API_MODULE_MAP")

    # ---- 4. any remaining decorated / undecorated literal -------------------
    fallback, fallback_basis = _fallback_module(modules)
    for req in requests:
        if req["module"] is None and fallback and req["origin"] != "public_api_hook":
            req["module"] = fallback
            req["module_basis"] = fallback_basis
    known = {r["symbol"] for r in requests}
    for start, end, kind, text, line in literals:
        if kind == "char":
            continue
        if any(a <= start < b for a, b in covered):
            continue          # already explained by a hook table
        value = text.strip()
        shape = _candidate_is_symbol(value)
        if shape in ("decorated", "undecorated") and value not in known:
            add(value, "decorated_literal", line,
                note="literal shaped like a %s symbol name outside a hook table" % shape)

    return {
        "mod_id": meta["raw"].get("id"),
        "metadata": meta,
        "modules": modules,
        "requests": _dedupe(requests),
        "preprocessor_notes": preproc_notes,
        "unresolved_hooks": unresolved_hooks,
        "macro_literals": macros,
        "defines_assumed": sorted(defines),
    }


MODULE_NAME_RE = re.compile(
    r"^[\w\-\.\+]{2,64}\.(?:dll|exe|cpl|drv|ocx|sys|efi|node|mui)$", re.I)


def _module_literal_in_span(literals, start: int, end: int):
    """First literal inside [start,end) that is shaped like a module name."""
    for lit_start, lit_end, kind, text, line in literals:
        if start <= lit_start < end:
            value = text.strip()
            if MODULE_NAME_RE.match(value):
                return value
    return None


def _module_for_hook_symbols(code: str, literals, table_name, modules: dict):
    """Resolve the module argument of the HookSymbols call that uses a table.

    Returns (module, basis). `basis` records how the answer was obtained so the
    report can distinguish an explicit literal from an inference.
    """
    for m in re.finditer(r"HookSymbols\s*\(", code):
        open_paren = m.end() - 1
        close = _balanced(code, open_paren, "(", ")")
        if close == -1:
            continue
        args = _split_top_level(code[open_paren + 1:close - 1])
        if not args:
            continue
        # the table is the second argument: only a call that passes *this* table
        # can say anything about its module.
        if table_name and (len(args) < 2 or table_name not in args[1]):
            continue
        arg = args[0].strip()
        if arg in ("nullptr", "NULL"):
            return modules.get("process_module"), "process-include"
        ident = re.match(r"([A-Za-z_]\w*)", arg)
        if ident and ident.group(1) in modules["variable_map"]:
            return modules["variable_map"][ident.group(1)], "variable"
        inline = _module_literal_in_span(literals, open_paren, open_paren + len(args[0]) + 2)
        if inline:
            return inline, "inline-literal"
        return None, "unresolved"
    return _fallback_module(modules)


def _fallback_module(modules: dict):
    """Last-resort module for a symbol request, and how it was obtained.

    Order: the only module the mod loads -> the only module in `@include` -> the
    target process, but only when `@include` is `*` (then the mod really does
    hook the process itself). Otherwise nothing is claimed.
    """
    loaded = sorted({e["module"] for e in modules.get("loaded", []) if e.get("module")})
    if len(loaded) == 1:
        return loaded[0], "inferred-single-module"
    includes = [i for i in modules.get("include_modules") or [] if i != "*"]
    if len(includes) == 1:
        return includes[0], "include-metadata"
    if modules.get("process_module") and not includes:
        return modules["process_module"], "process-include"
    return None, "none"


def _dedupe(requests):
    seen, out = {}, []
    for req in requests:
        key = (req["symbol"], req["module"], req["origin"])
        if key in seen:
            continue
        seen[key] = True
        out.append(req)
    # stable order: by line, then symbol
    out.sort(key=lambda r: (r["line"] or 0, r["symbol"]))
    return out


def extract_file(path: str) -> dict:
    with open(path, encoding="utf-8", errors="replace") as f:
        return extract_requests(f.read(), path=path)
