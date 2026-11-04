"""MSVC name handling for the symbol matrix -- structural, not a full demangler.

Windhawk matches symbol names by **exact string comparison** against the names
DIA reports for a PDB symbol: the *undecorated* name by default, or the
*decorated* name when the mod asks for `noUndecoratedSymbols`
(`src/windhawk/engine/mod.cpp`, `HookSymbolsSession::OnSymbolResolved`;
`symbol_enum.cpp` for the DIA enumeration with `get_undecoratedNameEx`).

On Linux we can read the decorated names straight out of the PDB, but the
undecorated text DIA produces needs a Microsoft/LLVM demangler. This module
therefore works structurally:

* `parse_decorated()`  -> qualified name (with MSVC back-references resolved),
  symbol kind (function/data/thunk), access, static/virtual flags, template
  arguments, and the raw parameter encoding;
* `parse_undecorated()`-> the same fields, parsed from the human-readable
  signature a mod writes in a `SYMBOL_HOOK` table;
* `compare()`          -> a score plus a *confidence* label.

Confidence labels end up in the JSON report:

* `exact`      -- byte-identical names (what Windhawk itself does), or a name
                  undecorated by `llvm-undname` when that tool is installed;
* `structural` -- same qualified name, same access/static/virtual shape and the
                  same parameter count, derived by this module;
* `partial`    -- same qualified name only (the parameter encoding contained a
                  construct this module does not decode);
* `weak`       -- matched only case-insensitively, or by a wildcard the mod
                  invented; Windhawk would *not* match these, so they are
                  reported as `ambiguous`.
"""
import re

__all__ = ["DecoratedName", "UndecoratedName", "parse_decorated",
           "parse_undecorated", "compare", "is_decorated", "looks_undecorated"]

# Function access/kind codes.
#
# x86-64 (the only architecture this bench builds for, see docs/limitations.md
# item 8) has a single calling convention and no near/far distinction, so MSVC
# re-purposed the codes; the mapping below is the one observed in Windows PDBs
# (Q = public member, U = public virtual, S = public static, Y = non-member
# function, ...). The classic x86 near/far table is kept for completeness.
# Codes outside these tables are reported as unknown and lower the confidence
# instead of producing a wrong verdict.
FUNCTION_CODES_X64 = {
    "A": ("private", False, False, False),    # access, static, virtual, thunk
    "E": ("private", False, True, False),
    "C": ("private", True, False, False),
    "I": ("protected", False, False, False),
    "M": ("protected", False, True, False),
    "K": ("protected", True, False, False),
    "Q": ("public", False, False, False),
    "U": ("public", False, True, False),
    "S": ("public", True, False, False),
    "Y": ("global", False, False, False),
    "Z": ("global", False, False, False),
    "G": ("private", False, False, True),     # adjustor thunk
    "T": ("public", False, False, True),
    "V": ("public", False, True, True),
    "N": ("protected", False, False, True),
}

FUNCTION_CODES_X86 = {
    "A": ("private", False, False, False),
    "B": ("private", False, False, False),
    "C": ("private", True, False, False),
    "D": ("private", True, False, False),
    "E": ("private", False, True, False),
    "F": ("private", False, True, False),
    "G": ("private", False, False, True),
    "H": ("private", False, False, True),
    "I": ("private", False, True, True),
    "J": ("private", False, True, True),
    "M": ("public", False, False, False),
    "N": ("public", False, False, False),
    "O": ("public", True, False, False),
    "P": ("public", True, False, False),
    "Q": ("public", False, True, False),
    "R": ("public", False, True, False),
    "S": ("public", False, False, True),
    "T": ("public", False, False, True),
    "U": ("public", False, True, True),
    "V": ("public", False, True, True),
    "X": ("protected", False, False, False),
    "Y": ("protected", False, False, False),
    "Z": ("protected", True, False, False),
    "0": ("protected", True, False, False),
    "1": ("protected", False, True, False),
    "2": ("protected", False, True, False),
    "3": ("protected", False, False, True),
    "4": ("protected", False, False, True),
    "5": ("protected", False, True, True),
    "6": ("protected", False, True, True),
    "7": ("global", False, False, False),
    "8": ("global", False, False, False),
    "9": ("global", False, False, True),
}

FUNCTION_CODES = FUNCTION_CODES_X64

# Data symbols: ?name@@<digit><type><access code>. The leading digit gives the
# storage class; the trailing access character is left opaque on purpose, since
# its meaning for data is not what a mod's signature text states.
DATA_CODES = {
    "0": ("private", True), "1": ("protected", True), "2": ("public", True),
    "3": ("global", False), "4": ("global", False), "5": ("global", True),
    "6": ("global", True),   # e.g. ??_7Class@@6B@  -> `vftable'
    "7": ("global", False),
}

# MSVC "special names": ??0 is a constructor, ??_G a complete-object
# constructor, ??_7 a vftable, ??4 operator=, and so on (LLVM
# MicrosoftDemangle::demangleSpecialIntrinsic / cvinfo.h). Anything not listed
# is reported as an unrecognised special name and lowers the confidence.
SPECIAL_NAMES = {
    "0": ("{ctor}", "constructor"),
    "1": ("{dtor}", "destructor"),
    "2": ("operator new", "operator"),
    "3": ("operator delete", "operator"),
    "4": ("operator=", "operator"),
    "5": ("operator>>", "operator"),
    "6": ("operator<<", "operator"),
    "7": ("operator!", "operator"),
    "8": ("operator==", "operator"),
    "9": ("operator!=", "operator"),
    "A": ("operator[]", "operator"),
    "B": ("operator", "conversion-operator"),
    "C": ("operator->", "operator"),
    "D": ("operator*", "operator"),
    "E": ("operator++", "operator"),
    "F": ("operator--", "operator"),
    "G": ("operator<", "operator"),
    "H": ("operator>", "operator"),
    "I": ("operator<=", "operator"),
    "J": ("operator>=", "operator"),
    "K": ("operator,", "operator"),
    "L": ("operator->*", "operator"),
    "M": ("operator/", "operator"),
    "N": ("operator%", "operator"),
    "O": ("operator/", "operator"),
    "P": ("operator%", "operator"),
    "Q": ("operator+=", "operator"),
    "R": ("operator-=", "operator"),
    "S": ("operator*=", "operator"),
    "_7": ("`vftable'", "vtable"),
    "_8": ("`vbtable'", "vtable"),
    "_9": ("`vdispmap'", "vtable"),
    "_A": ("`RTTI Type Descriptor'", "rtti"),
    "_D": ("`RTTI Complete Object Locator'", "rtti"),
    # `??_E` / `??_G` are the deleting destructors the compiler emits. Unlike
    # `??0` / `??1` they DO carry a return type (`void *`) and take one
    # `unsigned int` flags parameter, so they are not "no return type" names.
    "_E": ("`vector deleting destructor'", "deleting-destructor"),
    "_G": ("`scalar deleting destructor'", "deleting-destructor"),
    "_H": ("{ctor}", "base-object-constructor"),
    "_I": ("{ctor}", "virtual-base-constructor"),
    "_J": ("`local static thread guard'", "guard"),
    "_L": ("`local static guard'", "guard"),
    "_M": ("`local vftable'", "vtable"),
    "_P": ("`dynamic initializer'", "initializer"),
    "_Q": ("`dynamic atexit destructor'", "destructor"),
    "_U": ("operator new[]", "operator"),
    "_V": ("operator delete[]", "operator"),
}

CONVENTIONS = {"__cdecl", "__stdcall", "__fastcall", "__thiscall",
               "__vectorcall", "__clrcall", "__ptr64"}

# Simple one-character type codes (Microsoft cvinfo.h / undname).
SIMPLE_TYPES = set("BCDEFGHIJKLMNQTbcdeXZ")
POINTER_PREFIXES = set("APQR")          # pointer, reference, rvalue-ref, ...
CV_QUALIFIERS = set("ABCDENOPQRSTUVWX")  # const/volatile/__ptr64/__restrict markers

# After the access code, an x86-64 function decoration carries pointer-size and
# calling-convention modifiers before the return type: `QEAA` = public member
# (__ptr64, __cdecl, near), `SA` = public static, `YAH` = global returning int.
# The exact split is ambiguous from the text alone (the return type may itself
# start with E/I/A), so candidate strips are tried and the one whose type walk
# consumes the whole tail wins. See _parse_function_tail().
MODIFIER_CANDIDATES = ("EAA", "EAE", "EAI", "AEA", "AE", "EA", "E", "AA", "A", "")


class ParsedName(dict):
    __getattr__ = dict.get

    def __repr__(self):  # pragma: no cover
        return "%s(scope=%s, kind=%s, params=%s, confidence=%s)" % (
            type(self).__name__, self.get("qualified_name"), self.get("kind"),
            self.get("param_count"), self.get("confidence"))


class DecoratedName(ParsedName):
    pass


class UndecoratedName(ParsedName):
    pass


def is_decorated(text: str) -> bool:
    return bool(text) and text.startswith("?") and "@@" in text


def looks_undecorated(text: str) -> bool:
    """True for the human-readable signature form mods put in SYMBOL_HOOK."""
    if not text or text.startswith("?"):
        return False
    if "::" in text and "(" in text:
        return True
    return bool(re.search(r"__(cdecl|stdcall|fastcall|thiscall|vectorcall|clrcall)\s", text))


# ---------------------------------------------------------------------------
# decorated names
# ---------------------------------------------------------------------------
def parse_decorated(name: str, arch: str = "x86-64") -> DecoratedName:
    """Parse an MSVC decorated name. Unknown constructs degrade `confidence`."""
    out = DecoratedName(text=name, kind="unknown", access=None, is_static=False,
                        is_virtual=False, is_thunk=False, scope=[], qualified_name=None,
                        template=None, return_raw=None, params_raw=None,
                        param_count=None, confidence="partial", notes=[],
                        arch=arch, modifiers=None)
    if not name.startswith("?"):
        out["notes"].append("not a decorated name")
        out["confidence"] = "weak"
        out["qualified_name"] = name
        return out
    body = name[1:]
    if body.startswith("$$"):
        out["special_prefix"] = body[:3]
        body = body[3:]
        out["notes"].append("special decoration prefix %r" % out["special_prefix"])
    if body.startswith("?"):
        body = body[1:]
        special_name = None
        for length in (2, 1):           # "_G" style first, then "0".."L"
            key = body[:length]
            if key in SPECIAL_NAMES:
                special_name = SPECIAL_NAMES[key]
                body = body[length:]
                break
        if special_name is None:
            out["template"] = True      # ??$name@<args>@@
        else:
            out["special_name"] = special_name[0]
            out["special_kind"] = special_name[1]
            out["notes"].append("special name %r (%s)" % special_name)
    scope, rest, table = _read_qualified_name(body)
    if not scope:
        out["notes"].append("qualified name could not be read")
        return out
    out["scope_raw"] = scope
    base_names, templated = normalize_scope(list(reversed(scope)))
    out["scope"] = base_names
    out["template"] = bool(out.get("template")) or templated
    # MSVC writes the innermost identifier first: ?ShowStartView@XamlLauncher@@
    # is the undecorated XamlLauncher::ShowStartView.
    out["qualified_name"] = "::".join(base_names)
    special = out.get("special_kind")
    if special == "deleting-destructor":
        label = out.get("special_name") or "`scalar deleting destructor'"
        out["scope"] = base_names + [label]
        out["qualified_name"] = "::".join(out["scope"])
        out["kind"] = "destructor"
    elif special in ("constructor", "complete-object-constructor",
                     "base-object-constructor", "virtual-base-constructor"):
        # DIA prints `public: __cdecl AppMenu::AppMenu(...)` for ??0AppMenu@@...
        inner = base_names[-1] if base_names else ""
        out["scope"] = base_names + [inner]
        out["qualified_name"] = "::".join(out["scope"])
        out["kind"] = "constructor"
        out["no_return_type"] = True
    elif special in ("destructor",):
        inner = base_names[-1] if base_names else ""
        out["scope"] = base_names + ["~" + inner]
        out["qualified_name"] = "::".join(out["scope"])
        out["kind"] = "destructor"
        out["no_return_type"] = True
    elif special in ("vtable", "rtti", "guard", "initializer"):
        out["kind"] = "data"
    elif special:
        out["kind"] = special
    if not rest:
        out["notes"].append("name ends after the qualified name")
        return out

    codes = FUNCTION_CODES_X86 if arch == "x86" else FUNCTION_CODES_X64
    code = rest[0]
    tail = rest[1:]

    if code in DATA_CODES and _looks_like_data(code, tail):
        access, is_static = DATA_CODES[code]
        out.update(kind="data", access=access, is_static=is_static,
                   confidence="structural", type_raw=tail, data_code=code,
                   notes=out["notes"] + ["data symbol (storage class %r)" % code])
        return out

    if code in codes:
        access, is_static, is_virtual, is_thunk = codes[code]
        # a constructor/destructor keeps its specific kind: it is what tells the
        # comparison that there is no return type in the encoding.
        keep = out.get("kind") in ("constructor", "destructor")
        out.update(kind=out.get("kind") if keep else
                   ("thunk" if is_thunk else
                    ("static-function" if is_static else "function")),
                   access=access, is_static=is_static, is_virtual=is_virtual,
                   is_thunk=is_thunk, access_code=code)
    else:
        out.update(kind="function",
                   notes=out["notes"] + [
                       "unknown access/convention code %r for %s; the qualified "
                       "name is still compared" % (code, arch)])
        is_static = False

    _parse_function_tail(out, tail, is_static or bool(out.get("no_return_type")))
    return out


def _looks_like_data(code: str, tail: str) -> bool:
    """Distinguish `?x@C@@0HA` (data) from a function whose code is a digit."""
    if not tail:
        return True
    # Data decorations never end with the parameter terminator "@Z".
    return not tail.endswith("@Z")


def _parse_function_tail(out: DecoratedName, tail: str, is_static: bool):
    """Find the modifier strip whose type walk explains the whole tail.

    The tail is `<modifiers><return type><param types>@Z`, except for
    constructors/destructors which carry `<modifiers>@<param types>@Z`. The
    exact split between modifiers and return type is ambiguous from the text
    alone (a return type can itself start with E/I/A), so the candidate strips
    are tried and the one whose type walk consumes the whole tail exactly wins;
    the choice is recorded in the report.
    """
    end = tail.rfind("@Z")
    terminator_found = end != -1
    if end == -1:
        end = len(tail) - 1 if tail.endswith("Z") else len(tail)
    segment = tail[:end]
    no_return = bool(out.get("no_return_type"))
    if no_return:
        out["notes"].append("constructor/destructor encoding: no return type; the "
                            "'@' right after the modifiers marks that")

    best = None
    for strip in MODIFIER_CANDIDATES:
        if strip and not segment.startswith(strip):
            continue
        rest = segment[len(strip):]
        if no_return and rest.startswith("@"):
            rest = rest[1:]
        result = _walk_types(rest, no_return)
        if result is None:
            continue
        pos, count, understood = result
        score = (10 if pos == len(rest) else 0) + (2 if understood else 0) + \
                (1 if count is not None else 0) + (0 if strip else -1)
        if best is None or score > best[0]:
            best = (score, strip, rest, pos, count, understood)
    if best is None:
        out.update(params_raw=segment, param_count=None, confidence="partial",
                   modifiers=None, terminator="@Z" if terminator_found else None)
        out["notes"].append("parameter encoding could not be walked; only the "
                            "qualified name is compared")
        return
    _score, strip, rest, pos, count, understood = best
    out.update(modifiers=strip or None,
               return_raw=None if no_return else (rest[:pos_return_end(rest, False)] or None),
               params_raw=rest, param_count=count,
               terminator="@Z" if terminator_found else None)
    if understood and pos == len(rest):
        out["confidence"] = "structural"
    else:
        out["confidence"] = "partial"
        out["notes"].append("parameter encoding contains a construct this parser "
                            "does not decode (walked %d of %d chars); matched on "
                            "the qualified name only" % (pos, len(rest)))


def pos_return_end(rest: str, is_static: bool) -> int:
    if is_static:
        return 0
    pos, _ok = _skip_type(rest, 0)
    return pos if pos > 0 else 0


def _walk_types(segment: str, no_return: bool = False):
    """Walk `<return type><param types>`; returns (pos, param_count, understood).

    `no_return` is only true for constructors and destructors, whose encoding
    has no return type. Static member functions and global functions do carry
    one, and skipping it would count the return type as a parameter.
    """
    pos = 0
    understood = True
    if not no_return:
        new_pos, ok = _skip_type(segment, 0)
        if new_pos <= 0:
            return None
        pos, understood = new_pos, ok
    count = 0
    while pos < len(segment):
        if segment[pos] == "Z":
            break
        if segment[pos] == "X" and count == 0 and \
                (pos + 1 >= len(segment) or segment[pos + 1] == "Z"):
            # `XZ` encodes an *empty* parameter list, not one void parameter:
            # DIA prints such a function as `f(void)`, so counting it as one
            # parameter would make every zero-argument member function mismatch.
            pos += 1
            break
        new_pos, ok = _skip_type(segment, pos)
        if new_pos <= pos:
            return None
        understood = understood and ok
        pos = new_pos
        count += 1
    return pos, count, understood


def _read_qualified_name(body: str):
    """Read `<ident>@<ident>@@` with MSVC digit back-references and templates.

    Returns (scope_raw, rest, ident_table) where `scope_raw` keeps MSVC's
    innermost-first order and templated components as their raw decorated text.
    `0`-`9` refer to previously seen identifiers (0 = the first one);
    `?A<digits>@` is an anonymous namespace; `?$name@<args>@@` is a template.
    """
    scope, table = [], []
    i = 0
    while i < len(body):
        if body.startswith("@@", i):
            return scope, body[i + 2:], table
        if body[i] == "@":
            i += 1
            continue
        if body[i].isdigit():
            index = int(body[i])
            scope.append(table[index] if index < len(table) else "?backref%d" % index)
            i += 1
            continue
        if body.startswith("?$", i):
            end, _ok = _skip_name(body, i)
            if end <= i:
                end = len(body)
            ident = body[i:end]
            scope.append(ident)
            if len(table) < 10:
                table.append(ident)
            # The template's own "@@" terminates the qualified name.
            return scope, body[end:], table
        if body[i] == "?" and body[i + 1:i + 2] == "A":
            j = i + 2
            while j < len(body) and body[j] != "@":
                j += 1
            scope.append("`anonymous namespace'")
            i = j
            continue
        j = i
        while j < len(body) and body[j] not in "@0123456789":
            j += 1
        ident = body[i:j]
        if not ident:
            i += 1
            continue
        scope.append(ident)
        if len(table) < 10:
            table.append(ident)
        i = j
    return scope, "", table


TEMPLATE_COMPONENT_RE = re.compile(r"^\?\$(?P<name>[^@]+)@")


# The scope that terminates a decorated name or a template component:
# `@ident@ident@@` (innermost first) or just `@@`. The leading `@` of the first
# identifier is optional because a nested component may have consumed it.
SCOPE_TAIL_RE = re.compile(r"(?:@?(?:[A-Za-z_$][A-Za-z0-9_$]*|\d+))*@@")

# Single characters are type codes (D=char, H=int, V=class, ...), never scope
# identifiers; digits are back-references. A tail containing one of them is a
# template argument list, not a scope, and must be walked as types instead.
TYPE_CODE_CHARS = set("ABCDEFGHIJKLMNQTUVWXZ_$")


def _match_scope_tail(text: str, pos: int):
    """Match `<scope>@@` at `pos`; returns (end, scope_parts_outermost_first).

    Returns None when the run that looks like a scope actually contains type
    codes, which is how `?$char_traits@D@std@@` (argument `D`, scope `std`) is
    told apart from a scope called `D::std`.
    """
    m = SCOPE_TAIL_RE.match(text, pos)
    if not m or m.end() <= pos:
        return None
    raw = [p for p in text[pos:m.end() - 2].lstrip("@").split("@") if p]
    for part in raw:
        if len(part) < 2 or part[0] in TYPE_CODE_CHARS or part.isdigit():
            return None
    return m.end(), list(reversed(raw))


def _template_parts(text: str):
    """`?$basic_string@<args>@std@@` -> (["std"], "basic_string").

    Template arguments are dropped on purpose (see `normalize_scope`). The
    argument list is walked with the type scanner and the enclosing scope is
    recognised by its shape (`@ident@ident@@`), which is what separates
    `?$produce@UITaskbar@@X@impl@winrt@@` (scope winrt::impl) from a nested
    argument that also ends in `@@`.
    """
    if not text.startswith("?$"):
        return [], text
    i = 2
    j = text.find("@", i)
    if j == -1:
        return [], text[2:]
    name = text[i:j]
    pos = j + 1
    scope = []
    while pos < len(text):
        tail = _match_scope_tail(text, pos)
        if tail:
            scope = tail[1]
            break
        if text[pos] == "@":
            pos += 1
            continue
        new_pos, _ok = _skip_type(text, pos)
        if new_pos <= pos:
            break
        pos = new_pos
    return scope, name


def normalize_scope(scope) -> tuple:
    """Flatten a scope into base names without template arguments.

    Returns (names, had_templates). Template arguments are deliberately not
    compared: the two sides encode them differently (decorated
    `?$basic_string@DU?$char_traits@D@std@@...@@` vs the DIA text
    `class std::basic_string<char,struct std::char_traits<char>,...>`), and
    comparing them would need a full demangler. The report says so.
    """
    out, templated = [], False
    for part in scope or []:
        text = str(part)
        if text.startswith("?$"):
            prefix, name = _template_parts(text)
            out.extend(prefix)
            out.append(name)
            templated = True
            continue
        if "<" in text:
            depth, cut = 0, len(text)
            for idx, ch in enumerate(text):
                if ch == "<":
                    depth += 1
                    if depth == 1:
                        cut = idx
                elif ch == ">":
                    depth -= 1
            out.append(text[:cut].strip())
            templated = True
            continue
        out.append(text)
    return out, templated


def _skip_type(text: str, pos: int):
    """Advance over exactly one encoded type; returns (new_pos, understood).

    Implements the subset of the MSVC type grammar that Windows shell modules
    actually use: simple types, pointer/reference chains with __ptr64 and
    cv-qualifiers, named types (class/struct/union/enum) with templates, nested
    decorated names and function-pointer types. Anything else stops the walk and
    lowers the confidence of the comparison instead of guessing.
    """
    if pos >= len(text):
        return pos, False
    ch = text[pos]
    if ch == "_":                       # _J int64, _K uint64, _N, _O ...
        return (pos + 2, True) if pos + 1 < len(text) else (pos + 1, False)
    if ch in SIMPLE_TYPES:
        return pos + 1, True
    if ch == "$":
        return _skip_special(text, pos)
    if ch in POINTER_PREFIXES:          # P pointer, A reference, Q, R rvalue-ref
        pos += 1
        while pos < len(text) and text[pos] in "EIF":   # __ptr64/__ptr32/__unaligned
            pos += 1
        # cv-qualifiers of the pointed-to type: A = plain, B = const,
        # C = volatile. 'D' is deliberately absent: it is the code for `char`
        # (`PEAD` = char *__ptr64, `PEBD` = const char *__ptr64), and consuming
        # it here swallowed the next parameter into the pointer type.
        while pos < len(text) and text[pos] in "ABC":
            pos += 1
        return _skip_type(text, pos)
    if ch in "VUTW":                    # class / struct / union / enum
        if ch == "W" and text[pos + 1:pos + 2] != "4":
            return pos + 1, False
        return _skip_name(text, pos + (2 if ch == "W" else 1))
    if ch == "?":                       # nested decorated name (?AVFoo@@, ?A0x..@)
        return _skip_name(text, pos + 1)
    if ch.isdigit() and text[pos + 1:pos + 2] == "6":
        return _skip_function_type(text, pos + 2, offset=pos)
    if ch.isdigit():
        # A digit here is a type back-reference ("the Nth type seen before"),
        # which is a complete type expression.
        return pos + 1, True
    return pos + 1, False


def _skip_function_type(text: str, pos: int, offset: int = 0):
    """Walk `<callconv><return><params>@Z` (a function-pointer type)."""
    if pos < len(text) and text[pos] in "ABCDEFGHI":
        pos += 1
    while pos < len(text) and text[pos] in "ABCD":
        pos += 1
    understood = True
    new_pos, ok = _skip_type(text, pos)          # return type
    if new_pos <= pos:
        return offset + (new_pos - pos) if False else new_pos, False
    understood = ok
    pos = new_pos
    while pos < len(text) and not text.startswith("@Z", pos) and text[pos] != "Z":
        new_pos, ok = _skip_type(text, pos)
        if new_pos <= pos:
            return pos, False
        understood = understood and ok
        pos = new_pos
    if text.startswith("@Z", pos):
        return pos + 2, understood
    if pos < len(text) and text[pos] == "Z":
        return pos + 1, understood
    return pos, understood


def _skip_name(text: str, pos: int):
    """Consume a qualified name (possibly templated) up to and including `@@`."""
    understood = True
    while pos < len(text):
        if text.startswith("@@", pos):
            return pos + 2, understood
        if text[pos] == "@":
            pos += 1
            continue
        if text[pos].isdigit():
            pos += 1                    # back-reference to an earlier identifier
            # `V56@` is a name made only of back-references and ends with a
            # single '@', unlike ordinary names which end with '@@'.
            if pos < len(text) and text[pos] == "@" and text[pos + 1:pos + 2] != "@":
                return pos + 1, understood
            continue
        if text.startswith("?$", pos):  # template: ?$name@<args>@<scope>@@
            pos += 2
            while pos < len(text) and text[pos] != "@":
                pos += 1
            if text.startswith("@@", pos):
                return pos + 2, understood
            pos += 1                    # skip the '@' before the argument list
            while pos < len(text):
                tail = _match_scope_tail(text, pos)
                if tail:
                    return tail[0], understood
                if text[pos] == "@":
                    pos += 1
                    continue
                new_pos, ok = _skip_type(text, pos)
                understood = understood and ok
                if new_pos <= pos:
                    return pos, False
                pos = new_pos
            return pos, understood
        if text.startswith("?A", pos):  # anonymous namespace ?A0x...@
            j = pos + 2
            while j < len(text) and text[j] != "@":
                j += 1
            pos = j
            continue
        j = pos
        while j < len(text) and text[j] not in "@0123456789":
            j += 1
        if j == pos:
            pos += 1
            continue
        pos = j
    return pos, understood


def _skip_special(text: str, pos: int):
    if text.startswith("$$V", pos):
        return pos + 3, True
    if text[pos:pos + 3] == "$$C":
        # $$CB / $$CBV... : const qualifier used by some toolchains (Chromium).
        new_pos, ok = _skip_type(text, pos + 3)
        return new_pos, ok
    if text[pos:pos + 2] == "$$" and text[pos + 3:pos + 4] == "6":
        # $$A6A... / $$B6A... : a function-pointer type; reuse the '6' walker.
        return _skip_type(text[pos + 3:], 0) if False else _skip_function_type(
            text, pos + 4, offset=pos + 3)
    if text.startswith("$$", pos):
        j = pos + 2
        while j < len(text) and text[j] not in "@Z":
            j += 1
        return j, False
    if text.startswith("$0", pos):
        j = pos + 2
        while j < len(text) and (text[j].isalnum()):
            j += 1
        return j, True
    j = pos + 1
    while j < len(text) and text[j] not in "@Z":
        j += 1
    return j, False


# ---------------------------------------------------------------------------
# undecorated (human readable) signatures
# ---------------------------------------------------------------------------
_ACCESS_RE = re.compile(r"^(public|private|protected)\s*:\s*", re.I)
_QUALIFIER_RE = re.compile(r"^(static|virtual|abstract|explicit)\s+", re.I)
_CONVENTION_RE = re.compile(r"__(cdecl|stdcall|fastcall|thiscall|vectorcall|clrcall)\s+")
_TEMPLATE_RE = re.compile(r"^(.*?)\s*<(.*)>\s*$")


def parse_undecorated(text: str) -> UndecoratedName:
    """Parse `public: virtual long __cdecl A::B(int, class C *)` structurally."""
    out = UndecoratedName(text=text, kind="function", access=None, is_static=False,
                          is_virtual=False, is_thunk=False, scope=[], template=None,
                          return_raw=None, params_raw=None, param_count=None,
                          confidence="partial", notes=[])
    rest = text.strip()
    while True:
        m = _ACCESS_RE.match(rest)
        if m:
            out["access"] = m.group(1).lower()
            rest = rest[m.end():]
            continue
        m = _QUALIFIER_RE.match(rest)
        if m:
            word = m.group(1).lower()
            if word == "static":
                out["is_static"] = True
            elif word in ("virtual", "abstract"):
                out["is_virtual"] = True
            rest = rest[m.end():]
            continue
        break
    if not rest:
        out["notes"].append("empty signature after qualifiers")
        return out

    # split parameters
    paren = _find_open_paren(rest)
    if paren is None:
        out["kind"] = "data"
        out["qualified_name"] = _strip_convention(rest).strip()
        out["scope_raw"] = _split_scope(out["qualified_name"])
        out["scope"], _ = normalize_scope(out["scope_raw"])
        out["confidence"] = "partial"
        out["notes"].append("no parameter list: treated as a data symbol")
        return out
    params_text = rest[paren + 1:_matching_paren(rest, paren)]
    head = rest[:paren].strip()

    convention = None
    m = _CONVENTION_RE.search(head)
    if m:
        convention = "__" + m.group(1).lower()
        head = (head[:m.start()] + head[m.end():]).strip()
    out["calling_convention"] = convention

    # head is "<return type> <qualified name>"; the qualified name is the last
    # whitespace-free run that may contain :: and <...>.
    return_raw, name_part = _split_return_and_name(head)
    out["return_raw"] = return_raw
    m = _TEMPLATE_RE.match(name_part)
    if m:
        out["template"] = m.group(2)
        name_part = m.group(1).strip()
    out["qualified_name"] = name_part
    out["scope_raw"] = _split_scope(name_part)
    base_names, templated = normalize_scope(out["scope_raw"])
    out["scope"] = base_names
    out["template"] = bool(out.get("template")) or templated
    out["params_raw"] = params_text
    out["param_count"] = _count_params(params_text)
    out["confidence"] = "structural" if out["scope"] else "partial"
    if not out["scope"]:
        out["notes"].append("qualified name could not be parsed")
    return out


def _split_scope(name: str) -> list:
    """Split a qualified name on top-level `::` (ignoring template arguments)."""
    parts, depth, cur = [], 0, []
    i = 0
    while i < len(name):
        ch = name[i]
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if depth == 0 and ch == ":" and name[i + 1:i + 2] == ":":
            parts.append("".join(cur).strip())
            cur = []
            i += 2
            continue
        cur.append(ch)
        i += 1
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return [p for p in parts if p]


def _find_open_paren(text: str):
    depth = 0
    for i, ch in enumerate(text):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif ch == "(" and depth <= 0:
            return i
    return None


def _matching_paren(text: str, start: int) -> int:
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return len(text)


def _strip_convention(text: str) -> str:
    return _CONVENTION_RE.sub("", text)


def _split_return_and_name(head: str):
    """`long __cdecl A::B` -> ('long', 'A::B'); handles templated return types."""
    head = head.strip()
    depth = 0
    last_space = None
    for i, ch in enumerate(head):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif ch == " " and depth == 0:
            last_space = i
    if last_space is None:
        return None, head
    return head[:last_space].strip(), head[last_space + 1:].strip()


def _count_params(text: str):
    text = text.strip()
    if not text or text == "void":
        return 0
    depth, count = 0, 1
    for ch in text:
        if ch in "(<[" :
            depth += 1
        elif ch in ")>]":
            depth -= 1
        elif ch == "," and depth == 0:
            count += 1
    return count


def param_texts(text: str):
    """Top-level parameter strings of an undecorated signature."""
    out = text.strip()
    if not out or out == "void":
        return []
    depth, cur, parts = 0, [], []
    for ch in out:
        if ch in "(<[":
            depth += 1
        elif ch in ")>]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


# ---------------------------------------------------------------------------
# comparison
# ---------------------------------------------------------------------------
def compare(requested: str, candidate: str, arch: str = "x86-64") -> dict:
    """Compare a name a mod asked for with a decorated name found in a PDB.

    Returns {matched, confidence, score, notes}. `confidence` is one of exact /
    structural / partial / weak (see the module docstring).
    """
    notes = []
    if requested == candidate:
        return {"matched": True, "confidence": "exact", "score": 1000,
                "basis": "byte-identical", "notes": notes}
    if requested.lower() == candidate.lower():
        return {"matched": True, "confidence": "weak", "score": 120,
                "basis": "case-insensitive",
                "notes": ["names differ only in case; Windhawk compares exact "
                          "strings, so this would NOT match at runtime"]}
    if not is_decorated(candidate):
        return {"matched": False, "confidence": "weak", "score": 0,
                "basis": "candidate is not decorated", "notes": notes}

    if is_decorated(requested):
        # Two decorated names that differ: compare structurally too, because
        # back-references can be encoded differently by different compilers.
        a, b = parse_decorated(requested, arch), parse_decorated(candidate, arch)
        score, confidence, extra = _structural(a, b)
        if score > 0:
            return {"matched": True, "confidence": confidence, "score": score,
                    "basis": "decorated-vs-decorated structure",
                    "notes": notes + extra + ["requested name is itself decorated "
                                              "but not byte-identical"]}
        return {"matched": False, "confidence": confidence, "score": 0,
                "basis": "decorated names differ", "notes": notes + extra}

    if looks_undecorated(requested):
        a = parse_undecorated(requested)
        b = parse_decorated(candidate, arch)
        score, confidence, extra = _structural(a, b)
        if score > 0:
            return {"matched": True, "confidence": confidence, "score": score,
                    "basis": "undecorated request vs decorated PDB name",
                    "notes": notes + extra + [
                        "compared structurally: qualified name, access, "
                        "static/virtual and parameter count; DIA's exact "
                        "undecorated text is only reproducible with "
                        "llvm-undname"]}
        return {"matched": False, "confidence": confidence, "score": 0,
                "basis": "no structural match", "notes": notes + extra}

    # plain name (e.g. an export): exact match only, or prefix match on the
    # decorated form (extern "C" names appear as the plain name).
    if candidate == requested:
        return {"matched": True, "confidence": "exact", "score": 1000,
                "basis": "plain name", "notes": notes}
    if candidate.startswith("?%s@@3" % requested):
        return {"matched": True, "confidence": "structural", "score": 500,
                "basis": "data symbol with matching plain name", "notes": notes}
    return {"matched": False, "confidence": "weak", "score": 0,
            "basis": "plain name not found", "notes": notes}


def _structural(a: ParsedName, b: DecoratedName):
    notes = []
    if not a.get("scope") or not b.get("scope"):
        return 0, "partial", ["a qualified name could not be parsed"]
    if a["scope"] != b["scope"]:
        return 0, "partial", ["qualified names differ: %s vs %s"
                              % ("::".join(a["scope"]), "::".join(b["scope"]))]
    score = 400
    confidence = "structural"
    if a.get("kind") in ("data",) or b.get("kind") == "data":
        if a.get("kind") != b.get("kind") and a.get("kind") != "unknown":
            notes.append("kind mismatch (%s vs %s)" % (a.get("kind"), b.get("kind")))
            return 0, "partial", notes
        return score, "structural", notes + ["data symbol, name matches"]
    if a.get("access") and b.get("access") and a["access"] != b["access"]:
        notes.append("access mismatch (%s vs %s)" % (a["access"], b["access"]))
        return 0, "partial", notes
    if bool(a.get("is_virtual")) != bool(b.get("is_virtual")):
        notes.append("virtual/static mismatch (requested virtual=%s, PDB virtual=%s)"
                     % (a.get("is_virtual"), b.get("is_virtual")))
        return 0, "partial", notes
    if bool(a.get("is_static")) != bool(b.get("is_static")):
        notes.append("static mismatch (requested=%s, PDB=%s)"
                     % (a.get("is_static"), b.get("is_static")))
        score -= 100
    pa = a.get("param_count") if a.get("confidence") != "partial" else None
    pb = b.get("param_count") if b.get("confidence") != "partial" else None
    if pa is not None and pb is not None:
        if pa != pb:
            notes.append("the qualified name exists but the parameter count "
                         "differs (requested %s, PDB %s): this is a different "
                         "overload, and Windhawk's exact string comparison would "
                         "NOT bind the mod's name to it" % (pa, pb))
            return 300, "partial", notes
        score += 100
    else:
        confidence = "partial"
        notes.append("parameter count could not be decoded on one side "
                     "(requested=%s, pdb=%s); matched on the qualified name only"
                     % (pa, pb))
    if a.get("template") or b.get("template"):
        confidence = "partial" if confidence == "structural" else confidence
        notes.append("template arguments were not compared (a full demangler "
                     "would be needed); matched on the template base name")
    return score, confidence, notes
