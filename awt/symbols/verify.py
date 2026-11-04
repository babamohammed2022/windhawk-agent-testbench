"""Feature 1: check a mod's internal symbols against a matrix of Windows builds.

Pipeline for one (module, build) cell:

    module + build --winbindex/pinned--> PE signature --symbol server--> PE image
    PE debug directory --> PDB signature --symbol server--> PDB --> symbol list
    symbol list + requested name --match.py--> found / ambiguous / missing

Every step can fail, and a failure is *never* interpreted as "the symbol is not
there". `missing` requires a PDB that was downloaded, parsed and searched. When
anything earlier broke - no network, no signature for that build, a module that
does not exist on that build, an unreadable container - the cell is `unknown`
with the reason recorded verbatim.

This runs on Linux with no Windows component: the PE and PDB readers are the
pure-Python ones in `awt.pe` / `awt.pdb`, and name comparison is `awt.symbols.match`.
"""
import json
import os
import time

from .. import env as host_env
from .. import pe as pe_mod
from .. import pdb as pdb_mod
from . import demangle, extract, match, matrix as matrix_mod, symserver, table, winbindex

MSF_MAGIC_PREFIX = b"Microsoft C/C++ MSF 7.00"

DISCLAIMER = (
    "A 'found' verdict means the symbol name the mod asks for exists in the "
    "public PDB Microsoft ships for that exact binary. It does NOT mean the hook "
    "works: nothing was executed, no Windows code ran, and a symbol that exists "
    "can still have a different signature or behaviour than the mod assumes. "
    "'missing' means the PDB was read and the name is not in it. 'unknown' means "
    "the check could not be performed (no network, no signature, unreadable PDB) "
    "and makes no claim either way."
)


def _index_cache_path(cache_dir, module, pe_signature):
    safe = "".join(c for c in pe_signature if c.isalnum())
    return os.path.join(cache_dir or host_env.cache_dir("symbols"), "index",
                        module.lower(), safe + ".json")


def _load_cached_index(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("rows")
        if not isinstance(rows, list):
            return None, "cached index has no rows"
        return pdb_mod.PdbFile.from_index_rows(rows, source="cache:" + path), \
            data.get("info") or {}
    except (OSError, ValueError) as exc:
        return None, "cached index unreadable: %s" % exc


def _store_cached_index(path, pdb_file, info):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"rows": pdb_file.index_rows(), "info": info,
                       "written": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                      f)
        return True
    except OSError:
        return False


class Verifier:
    """Holds the servers, caches and counters for one run."""

    def __init__(self, matrix, arch=None, offline=None, verbose=False,
                 cross_check=False):
        self.matrix = matrix
        self.arch = (arch or matrix.get("arch") or "x86-64").replace("x86_64", "x86-64")
        server_cfg = matrix.get("symbol_server") or {}
        self.server = symserver.SymbolServer(
            base_url=server_cfg.get("base_url"),
            cache_dir=server_cfg.get("cache_dir"),
            offline=offline, timeout=server_cfg.get("timeout_seconds") or 20,
            attempts=server_cfg.get("attempts") or 2, verbose=verbose)
        index_cfg = matrix.get("winbindex") or {}
        self.resolver = winbindex.Resolver(
            cache_dir=index_cfg.get("cache_dir"),
            offline=offline if index_cfg.get("enabled", True) is not False else True,
            timeout=index_cfg.get("timeout_seconds") or 25, verbose=verbose,
            pinned=matrix.get("pinned_signatures"))
        self.verbose = verbose
        self.cross_check = cross_check
        self.limit = int((matrix.get("limits") or {}).get("max_symbols_per_module")
                         or 400000)
        self.sleep = float((matrix.get("limits") or {}).get("sleep_between_downloads")
                           or 0)
        self.cells = {}            # (module, build_id) -> state dict
        self.notes = []
        self.started = time.time()

    # ---- one (module, build) ----------------------------------------------
    def module_state(self, module, build):
        key = (module.lower(), build["id"])
        if key in self.cells:
            return self.cells[key]
        state = self._resolve_module(module, build)
        self.cells[key] = state
        return state

    def _resolve_module(self, module, build):
        build_id = build["id"]
        state = {
            "module": module, "build": build_id, "label": build.get("label"),
            "channel": build.get("channel"), "available": False,
            "reason": None, "pe": None, "pdb": None, "symbol_count": None,
            "symbols": None, "warnings": [], "source": None,
        }
        local = (self.matrix.get("local_modules") or {}).get(module.lower())
        if local and local.get("pe"):
            return self._from_local_pe(state, local["pe"])

        entry, note = self.resolver.resolve(module, build_id,
                                            arch="x86" if self.arch == "x86" else "amd64")
        if entry is None:
            state["reason"] = "signature not resolvable: %s" % note
            return state
        state["pe_signature"] = entry["signature"]
        state["pe"] = {"signature": entry["signature"],
                       "timestamp": entry["timestamp"],
                       "virtual_size": entry["virtual_size"],
                       "version": entry.get("version"),
                       "signature_source": entry.get("source"),
                       "channel": entry.get("channel")}

        pe_path, pe_note = self.server.get(module, entry["signature"], kind="pe",
                                           expect_magic=b"MZ")
        state["pe"]["fetch"] = pe_note
        if pe_path is None:
            state["reason"] = "PE not available: %s" % pe_note
            return state
        return self._from_local_pe(state, pe_path, downloaded=True)

    def _from_local_pe(self, state, pe_path, downloaded=False):
        try:
            image = pe_mod.read(pe_path)
        except (OSError, pe_mod.PeError) as exc:
            state["reason"] = "PE unreadable: %s" % exc
            return state
        cv = image.codeview()
        state["source"] = pe_path
        state["pe"] = dict(state.get("pe") or {})
        state["pe"].update({"path": pe_path, "machine": image.machine_name,
                            "timestamp": image.timestamp,
                            "size_of_image": image.size_of_image,
                            "pe_signature": image.summary()["pe_signature"],
                            "downloaded": bool(downloaded)})
        if not cv:
            state["reason"] = ("PE has no CodeView debug record, so its PDB cannot "
                               "be identified")
            return state
        state["pdb"] = {"name": cv["pdb_name"], "path_in_image": cv["pdb_path"],
                        "guid": cv["guid"], "age": cv["age"],
                        "signature": cv["signature"]}

        cache_path = _index_cache_path(self.server.cache_dir, state["module"],
                                       state["pe"].get("pe_signature") or
                                       state["pe"].get("signature") or "local")
        cached, info = _load_cached_index(cache_path)
        if cached is not None:
            state.update({"available": True, "symbols": cached,
                          "symbol_count": len(cached.symbols()),
                          "index_cache": cache_path,
                          "pdb_info": info,
                          "warnings": list(info.get("warnings") or [])})
            return state

        pdb_path, pdb_note = self.server.get(cv["pdb_name"], cv["signature"],
                                             kind="pdb")
        state["pdb"]["fetch"] = pdb_note
        if pdb_path is None:
            if (self.matrix.get("local_modules") or {}).get(state["module"].lower(), {}).get("pdb"):
                pdb_path = self.matrix["local_modules"][state["module"].lower()]["pdb"]
                state["pdb"]["fetch"] = "local file %s" % pdb_path
            else:
                state["reason"] = "PDB not available: %s" % pdb_note
                return state
        try:
            with open(pdb_path, "rb") as f:
                head = f.read(len(MSF_MAGIC_PREFIX))
            if not head.startswith(MSF_MAGIC_PREFIX):
                state["reason"] = ("downloaded %s is not an MSF 7.0 PDB (starts %r)"
                                   % (cv["pdb_name"], head[:16]))
                return state
            pdb_file = pdb_mod.open(pdb_path)
        except (OSError, pdb_mod.PdbError, ValueError) as exc:
            state["reason"] = "PDB unreadable: %s" % exc
            return state

        symbols = pdb_file.symbols()[:self.limit]
        info = pdb_file.info()
        state.update({
            "available": True, "symbols": pdb_file,
            "symbol_count": len(symbols), "pdb_path": pdb_path,
            "pdb_info": {"symbol_count": info.get("symbol_count"),
                         "sections": info.get("sections"),
                         "modules": info.get("modules"),
                         "machine": info.get("machine"),
                         "record_areas": info.get("record_areas"),
                         "warnings": info.get("warnings"),
                         "analyzer": pdb_mod.ANALYZER["name"]},
            "warnings": list(info.get("warnings") or []),
        })
        if self.cross_check:
            state["cross_check"] = cross_check_pdb(pdb_path, symbols)
        _store_cached_index(cache_path, pdb_file, state["pdb_info"])
        state["index_cache"] = cache_path
        if self.sleep:
            time.sleep(self.sleep)
        return state

    # ---- one request ------------------------------------------------------
    def check_request(self, request, build):
        module = request.get("module")
        if not module:
            return {"outcome": "unknown", "confidence": "none", "matched_name": None,
                    "rva": None, "candidates": [], "basis": "no target module",
                    "notes": ["the extractor could not determine which module this "
                              "symbol belongs to (module_basis=%r), so nothing was "
                              "looked up" % request.get("module_basis")],
                    "alternatives_tried": []}
        state = self.module_state(module, build)
        if not state.get("available"):
            return {"outcome": "unknown", "confidence": "none", "matched_name": None,
                    "rva": None, "candidates": [],
                    "basis": state.get("reason") or "module unavailable",
                    "notes": [state.get("reason") or "module unavailable"],
                    "alternatives_tried": []}
        symbols = state["symbols"].symbols()
        res = match.match_request(request, symbols, arch=self.arch)
        if state.get("warnings"):
            res["notes"] = list(res.get("notes") or []) + [
                "PDB reader warning: %s" % w for w in state["warnings"][:3]]
        return res

    # ---- whole mod --------------------------------------------------------
    def run(self, requests, builds):
        symbols_out = []
        for request in requests:
            cells = {}
            for build in builds:
                cells[build["id"]] = self.check_request(request, build)
            outcomes = [c["outcome"] for c in cells.values()]
            symbols_out.append({
                "symbol": request.get("symbol"),
                "kind": request.get("kind"),
                "module": request.get("module"),
                "module_basis": request.get("module_basis"),
                "origin": request.get("origin"),
                "line": request.get("line"),
                "optional": bool(request.get("optional")),
                "alternatives": request.get("alternatives") or [],
                "api": request.get("api"),
                "outcome": _overall(outcomes),
                "builds": cells,
            })
        return symbols_out


def _overall(outcomes):
    """One word for the row: the weakest claim that is still true everywhere."""
    if not outcomes:
        return "unknown"
    if all(o == "found" for o in outcomes):
        return "found"
    if any(o == "unknown" for o in outcomes):
        return "unknown" if all(o in ("unknown", "found") for o in outcomes) \
            else "mixed"
    if any(o == "ambiguous" for o in outcomes):
        return "ambiguous"
    if any(o == "found" for o in outcomes):
        return "mixed"
    return "missing"


def cross_check_pdb(pdb_path, symbols):
    """Optional second opinion from llvm-pdbutil, when the host has it."""
    tool = host_env.which("llvm-pdbutil")
    if not tool:
        return {"attempted": False,
                "reason": "llvm-pdbutil is not installed on this host"}
    import subprocess
    try:
        proc = subprocess.run([tool, "dump", "--symbols", pdb_path],
                              capture_output=True, text=True, timeout=300)
    except Exception as exc:                            # pragma: no cover
        return {"attempted": True, "ok": False, "reason": str(exc)}
    names = set()
    for line in proc.stdout.splitlines():
        for token in line.split():
            if token.startswith("?") or token.isidentifier():
                names.add(token)
    ours = set(s.get("name") for s in symbols if s.get("name"))
    return {"attempted": True, "ok": proc.returncode == 0,
            "tool": tool, "our_symbols": len(ours),
            "shared_names": len(ours & names),
            "only_in_ours": sorted(ours - names)[:20],
            "note": ("token-level comparison of llvm-pdbutil output; a name list "
                     "only, not a byte comparison")}


def summarise(symbol_rows, module_states, builds):
    counts = {"found": 0, "missing": 0, "ambiguous": 0, "unknown": 0, "mixed": 0}
    per_build = dict((b["id"], {"found": 0, "missing": 0, "ambiguous": 0,
                                "unknown": 0}) for b in builds)
    optional_missing = 0
    for row in symbol_rows:
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
        for build_id, cell in row["builds"].items():
            if build_id in per_build:
                key = cell["outcome"] if cell["outcome"] in per_build[build_id] \
                    else "unknown"
                per_build[build_id][key] += 1
                if cell["outcome"] == "missing" and row["optional"]:
                    optional_missing += 1
    return {
        "symbols": len(symbol_rows),
        "builds": len(builds),
        "cells": len(symbol_rows) * len(builds),
        "row_outcomes": counts,
        "per_build": per_build,
        "optional_symbols_missing": optional_missing,
        "modules_available": sum(1 for s in module_states if s.get("available")),
        "modules_unavailable": sum(1 for s in module_states if not s.get("available")),
    }


def verify_mod(mod_path, matrix_path=None, builds=None, modules=None, arch=None,
               offline=None, verbose=False, cross_check=False, use_llvm_undname=False,
               requests=None, max_requests=0):
    """Check one mod and return the `symbol_matrix` report section."""
    matrix = matrix_mod.load(matrix_path)
    if arch:
        matrix["arch"] = arch
    selected = matrix_mod.select_builds(matrix, builds)
    extraction = requests if requests is not None else extract.extract_file(mod_path)
    all_requests = list(extraction.get("requests") or [])
    if modules:
        wanted = set(m.lower() for m in modules)
        all_requests = [r for r in all_requests
                        if (r.get("module") or "").lower() in wanted]
    # Only requests that name a symbol inside a Windows module are checkable;
    # public-API hooks (Wh_SetFunctionHook on an exported API) are kept in the
    # report for completeness but flagged as not matrix-checked.
    checkable = [r for r in all_requests
                 if r.get("origin") in ("symbol_hook_table", "decorated_literal",
                                        "find_symbol_compare")]
    other = [r for r in all_requests if r not in checkable]
    cap = max_requests or int((matrix.get("limits") or {}).get("max_requests") or 0)
    if cap and len(checkable) > cap:
        matrix["notes"].append("only the first %d of %d symbol requests were "
                               "checked (--max-requests)" % (cap, len(checkable)))
        checkable = checkable[:cap]

    verifier = Verifier(matrix, arch=matrix.get("arch"), offline=offline,
                        verbose=verbose, cross_check=cross_check)
    rows = verifier.run(checkable, selected)
    states = list(verifier.cells.values())

    section = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mod_file": os.path.basename(str(mod_path)),
        "mod_id": extraction.get("mod_id"),
        "arch": verifier.arch,
        "disclaimer": DISCLAIMER,
        "matrix": matrix_mod.describe(matrix),
        "extraction": {
            "requests": len(all_requests),
            "checked": len(checkable),
            "not_matrix_checked": len(other),
            "by_origin": _count_by(all_requests, "origin"),
            "by_kind": _count_by(all_requests, "kind"),
            "modules": sorted({(r.get("module") or "") for r in all_requests} - {""}),
            "module_basis": _count_by(all_requests, "module_basis"),
            "preprocessor_notes": extraction.get("preprocessor_notes") or [],
            "defines_assumed": extraction.get("defines_assumed") or [],
        },
        "modules": [_module_view(s) for s in states],
        "symbols": rows,
        "summary": summarise(rows, states, selected),
        "network": {
            "symbol_server": verifier.server.describe(),
            "server_probe": verifier.server.probe(),
            "winbindex": dict(winbindex.describe(),
                              **{"offline": verifier.resolver.offline,
                                 "notes": verifier.resolver.notes[:10]}),
            "offline": bool(verifier.server.offline),
        },
        "tooling": match.describe_environment(),
        "seconds": round(time.time() - verifier.started, 2),
        "notes": list(matrix["notes"]) + list(verifier.notes),
    }
    if use_llvm_undname:
        section["tooling"]["undecoration_requested"] = "llvm-undname"
    return section


def _module_view(state):
    out = {k: v for k, v in state.items() if k not in ("symbols",)}
    out["available"] = bool(state.get("available"))
    if not out.get("reason"):
        out.pop("reason", None)
    return out


def _count_by(rows, key):
    counts = {}
    for row in rows:
        value = row.get(key) or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return counts


def render(section, max_rows=0, width=None):
    return table.render(section, max_rows=max_rows, width=width)


def demangler_info():
    return {"implementation": "awt/symbols/demangle.py",
            "coverage": "MSVC x86 and x86-64 decorated names, templates, "
                        "special names (ctor/dtor/vftable/operator), data symbols",
            "limits": ["template arguments are not compared",
                       "names it cannot fully decode are reported as confidence "
                       "'partial' and only their qualified name is compared"],
            "llvm_undname": match.find_undname() or None}
