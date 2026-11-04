"""Decide whether a symbol a mod asked for exists in a PDB.

The reference implementation is the Windhawk engine itself
(`src/windhawk/engine/symbol_enum.cpp` + `mod.cpp`): it enumerates public and
global symbols through DIA, asks DIA for `get_undecoratedNameEx` and then
compares the string a mod wrote with the strings DIA produced, **byte for
byte**. The first alternative in a `SYMBOL_HOOK` entry that matches wins.

We cannot run DIA on Linux, so this module reproduces the decision as closely
as it can and *reports how closely*:

* `exact`        - byte-identical string, the same test Windhawk performs.
* `structural`   - the decorated PDB name was decoded and its qualified name,
                   access, static/virtual and parameter count agree with the
                   undecorated text the mod wrote. This is evidence the symbol
                   exists; it is not evidence the strings are identical.
* `partial`      - one side could only be decoded partially (template arguments,
                   an encoding this demangler does not cover). Names agree,
                   signatures were not compared.
* `weak`         - case-insensitive agreement only. Windhawk would NOT match
                   this at runtime, so it is reported as `ambiguous`, never
                   `found`.

Anything the tool could not look at (no PDB, no network, unreadable container)
is `unknown` and is produced by `verify.py`, not here: `missing` from this
module always means "the PDB was read and the symbol is not in it".
"""
import fnmatch
import os
import re

from . import demangle
from .. import env as host_env

# Optional accelerator: llvm-undname produces DIA-compatible undecorated text.
# When it is absent the pure-Python demangler in demangle.py is used and every
# structural verdict says so.
UNDNAME_CANDIDATES = ("llvm-undname", "llvm-undname-14", "llvm-undname-15",
                      "llvm-undname-16", "llvm-undname-17", "llvm-undname-18")

WILDCARD_RE = re.compile(r"[*?\[\]]")
X86_NAME_RE = re.compile(r"^[@_][A-Za-z_$][A-Za-z0-9_$]*@\d{1,4}$")

TOOL_INFO = {
    "matcher": "awt-match",
    "reference": "Windhawk engine symbol_enum.cpp/mod.cpp (DIA, exact string compare)",
    "demangler": "awt-demangle (partial MSVC x86/x64 decorated-name decoder)",
}


def find_undname() -> str:
    """Path of llvm-undname when the host has it, else ''."""
    for name in UNDNAME_CANDIDATES:
        path = host_env.which(name)
        if path:
            return path
    return ""


def undecorate_with_llvm(names, arch="x86-64"):
    """Run llvm-undname over a batch of decorated names.

    Returns {decorated: undecorated} or None when the tool is unavailable or
    failed. Flags mirror the engine's `get_undecoratedNameEx(0x20800)`: no
    access specifier is not requested, we want the same text DIA prints.
    """
    tool = find_undname()
    if not tool or not names:
        return None
    import subprocess
    try:
        proc = subprocess.run([tool] + list(names), capture_output=True,
                              text=True, timeout=60)
    except Exception:                                   # pragma: no cover
        return None
    if proc.returncode != 0:
        return None
    lines = proc.stdout.split("\n")
    out = {}
    for name, line in zip(names, lines):
        line = line.strip()
        if line and line != name:
            out[name] = line
    return out or None


def base_keys(text: str, kind: str = None) -> list:
    """Cheap identifiers that must appear in any decorated name for `text`.

    Used only to narrow a 100k-symbol table before the expensive comparison;
    a false negative here would hide a real match, so both the innermost name
    and the full qualified tail are returned.
    """
    text = (text or "").strip()
    if not text:
        return []
    if demangle.is_decorated(text):
        parsed = demangle.parse_decorated(text)
        scope = list(parsed.get("scope") or [])
        keys = [scope[-1]] if scope else []
        if len(scope) > 1:
            keys.append(scope[-2])
        return [k for k in keys if k and not k.startswith("`")]
    if demangle.looks_undecorated(text):
        parsed = demangle.parse_undecorated(text)
        scope = list(parsed.get("scope") or [])
        keys = [scope[-1]] if scope else []
        if len(scope) > 1:
            keys.append(scope[-2])
        return [k for k in keys if k and not k.startswith("`")]
    # plain C name / export
    return [text.split("::")[-1]] if text else []


def _candidate_prefilter(symbols, keys):
    """Symbols whose decorated name could plausibly carry one of `keys`."""
    if not keys:
        return list(symbols)
    out = []
    for sym in symbols:
        name = sym.get("name") or ""
        folded = name.lower()          # a case-only difference is still a candidate:
        for key in keys:               # it has to be reported, not filtered away
            low = key.lower()
            if folded == low or ("@%s@" % low) in folded \
                    or folded.startswith("?%s@" % low) \
                    or ("@%s@@@" % low) in folded or low in folded:
                out.append(sym)
                break
    return out


PLAIN_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _is_wildcard(text: str) -> bool:
    """A mod name that uses fnmatch syntax (Windhawk itself does not).

    Careful: `*` is also how DIA writes a pointer (`struct HWND__ * __ptr64`) and
    `?` starts every decorated name, so a text that already parses as a symbol is
    never treated as a pattern.
    """
    if demangle.is_decorated(text) or demangle.looks_undecorated(text):
        return False
    if PLAIN_NAME_RE.match(text) or X86_NAME_RE.match(text):
        return False
    return bool(WILDCARD_RE.search(text))


def match_one(request_text, symbols, arch="x86-64", kind=None, undname_map=None):
    """Compare one requested name against a PDB's symbol list.

    Returns {outcome, confidence, matched_name, rva, candidates, notes, basis}.
    `outcome` is found / ambiguous / missing; callers add `unknown` themselves
    when the PDB could not be read.
    """
    notes = []
    request_text = (request_text or "").strip()
    if not request_text:
        return {"outcome": "missing", "confidence": "none", "matched_name": None,
                "rva": None, "candidates": [], "notes": ["empty symbol name"],
                "basis": "empty"}

    if not symbols:
        return {"outcome": "unknown", "confidence": "none", "matched_name": None,
                "rva": None, "candidates": [],
                "notes": ["the PDB exposed no symbols to compare against"],
                "basis": "no-symbols"}

    wildcard = _is_wildcard(request_text)
    if wildcard:
        notes.append("request contains wildcard characters; Windhawk performs no "
                     "wildcard matching, so this verdict is indicative only")
        hits = [s for s in symbols
                if fnmatch.fnmatchcase(s.get("name") or "", request_text)]
        return _verdict(hits, notes, "wildcard", request_text)

    keys = base_keys(request_text, kind)
    pool = _candidate_prefilter(symbols, keys)

    # 1. the test Windhawk actually performs: byte-identical decorated name.
    exact = [s for s in pool if (s.get("name") or "") == request_text]
    if exact:
        return _verdict(exact, notes, "byte-identical name", request_text,
                        confidence="exact")

    # 2. byte-identical *undecorated* name (the engine's normal path).
    if undname_map:
        same = [s for s in pool
                if undname_map.get(s.get("name") or "") == request_text]
        if same:
            return _verdict(same, notes + ["matched llvm-undname output byte for byte"],
                            "llvm-undname text", request_text, confidence="exact")

    # 3. structural comparison with the built-in demangler.
    hits, best = [], None
    for sym in pool:
        name = sym.get("name") or ""
        if not demangle.is_decorated(name) and name != request_text:
            # a plain C symbol can still be an exact match for a plain request
            if name == request_text:
                hits.append((sym, {"matched": True, "confidence": "exact",
                                   "score": 1000, "basis": "plain name",
                                   "notes": []}))
            continue
        res = demangle.compare(request_text, name, arch=arch)
        if res.get("matched"):
            hits.append((sym, res))
            if best is None or res["score"] > best[1]["score"]:
                best = (sym, res)
    if not hits:
        out = {"outcome": "missing", "confidence": "none", "matched_name": None,
               "rva": None, "candidates": [], "basis": "no structural match",
               "notes": notes + ["PDB was read successfully; %d symbol(s) carried "
                                 "a related identifier and none matched"
                                 % len(pool)]}
        return out

    confidence = best[1]["confidence"] if best else "partial"
    if confidence == "weak":
        notes.append("the only agreement is case-insensitive; Windhawk compares "
                     "exact strings, so this symbol would NOT resolve at runtime")
        return _verdict([h[0] for h in hits], notes, "case-insensitive only",
                        request_text, confidence="weak", force="ambiguous")
    if best:
        notes = notes + list(best[1].get("notes") or [])
    return _verdict([h[0] for h in hits], notes, best[1]["basis"] if best else "?",
                    request_text, confidence=confidence)


def _verdict(hits, notes, basis, request_text, confidence=None, force=None):
    hits = [h for h in hits if h]
    seen, unique = set(), []
    for hit in hits:
        key = (hit.get("name"), hit.get("rva"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(hit)
    hits = unique
    if not hits:
        return {"outcome": force or "missing", "confidence": "none",
                "matched_name": None, "rva": None, "candidates": [],
                "basis": basis, "notes": notes}
    names = sorted({(h.get("name") or "") for h in hits})
    rvas = sorted({h.get("rva") for h in hits if h.get("rva") is not None})
    outcome = force
    if outcome is None:
        # one name at one address is a find; one name at two addresses, or two
        # names, is ambiguous - Windhawk takes the first exact match, which a PDB
        # alone does not order.
        outcome = "found" if len(names) == 1 and len(rvas) <= 1 else "ambiguous"
    if outcome == "ambiguous":
        notes = notes + ["%d symbols matched (%s); Windhawk takes the first exact "
                         "match, which is not deterministic from a PDB alone"
                         % (len(hits), ", ".join(names[:3]))]
    conf = confidence or (hits[0].get("confidence") if isinstance(hits[0], dict)
                          and "confidence" in hits[0] else "exact")
    return {
        "outcome": outcome,
        "confidence": conf,
        "matched_name": names[0],
        "rva": rvas[0] if rvas else None,
        "rvas": rvas,
        "candidates": [{"name": h.get("name"), "rva": h.get("rva"),
                        "kind": h.get("kind"), "source": h.get("source")}
                       for h in hits[:8]],
        "basis": basis,
        "notes": notes,
    }


def match_request(request, symbols, arch="x86-64", undname_map=None):
    """Match a whole `SymbolRequest`, honouring its alternative names.

    A `SYMBOL_HOOK` entry can list several names; Windhawk stops at the first
    exact match. Alternatives are therefore tried in order and the first
    non-`missing` result wins, with the alternatives that were tried recorded.
    """
    alternatives = request.get("alternatives") or [request.get("symbol")]
    tried = []
    best = None
    for alt in alternatives:
        res = match_one(alt, symbols, arch=arch, kind=request.get("kind"),
                        undname_map=undname_map)
        res["requested"] = alt
        tried.append({"name": alt, "outcome": res["outcome"],
                      "confidence": res["confidence"]})
        if res["outcome"] == "found" and res["confidence"] == "exact":
            best = res
            break
        if best is None or _rank(res) > _rank(best):
            best = res
    best = dict(best)
    best["alternatives_tried"] = tried
    if request.get("optional") and best["outcome"] == "missing":
        best["notes"] = list(best.get("notes") or []) + [
            "the mod marks this symbol optional: a miss is not an error for the "
            "mod, but it does mean the hook is inert on this build"]
    return best


_RANK = {"found": 3, "ambiguous": 2, "unknown": 1, "missing": 0}
_CONF = {"exact": 3, "structural": 2, "partial": 1, "weak": 0, "none": -1}


def _rank(res):
    return (_RANK.get(res.get("outcome"), 0),
            _CONF.get(res.get("confidence"), -1),
            res.get("score", 0) or 0)


def match_all(requests, symbols_by_module, arch="x86-64", use_llvm_undname=False):
    """Match every request against the symbols of its module."""
    undname_maps = {}
    out = []
    for req in requests:
        module = (req.get("module") or "").lower()
        symbols = symbols_by_module.get(module)
        if symbols is None:
            out.append({"request": req,
                        "result": {"outcome": "unknown", "confidence": "none",
                                   "matched_name": None, "rva": None,
                                   "candidates": [], "basis": "module unavailable",
                                   "notes": ["no PDB was read for %r"
                                             % (req.get("module") or None)],
                                   "alternatives_tried": []}})
            continue
        if use_llvm_undname and module not in undname_maps:
            names = [s.get("name") for s in symbols[:4000]
                     if demangle.is_decorated(s.get("name") or "")]
            undname_maps[module] = undecorate_with_llvm(names, arch) or {}
        out.append({"request": req,
                    "result": match_request(req, symbols, arch=arch,
                                            undname_map=undname_maps.get(module))})
    return out


def describe_environment():
    """Provenance block for the report: what actually did the matching."""
    undname = find_undname()
    return dict(TOOL_INFO, **{
        "llvm_undname": undname or None,
        "undecoration": ("llvm-undname (DIA-compatible text)" if undname else
                         "built-in partial MSVC demangler"),
        "pdb_analyzer": __import__("awt.pdb", fromlist=["ANALYZER"]).ANALYZER,
        "wildcards": "fnmatch, reported as indicative (Windhawk matches exact strings)",
        "cwd": os.getcwd(),
    })
