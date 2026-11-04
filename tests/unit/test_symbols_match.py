"""Unit tests for `awt.symbols.match`: the found / ambiguous / missing decision.

The symbol tables here are plain dicts, the shape `awt.pdb.PdbFile.symbols()`
returns, so the matcher is tested without a PDB and without a network.
"""
from awt.symbols import match
from tests.unit.fixtures import real_symbols as R

PDB_SYMBOLS = [
    {"name": "?Invoke@CTaskFrame@@UEAAJXZ", "rva": 0x20678, "kind": "public",
     "source": "public"},
    {"name": "?WndProc@CTaskFrame@@MEAA_JPEAUHWND__@@I_K_J@Z", "rva": 0x21100,
     "kind": "public", "source": "public"},
    {"name": "DllGetClassObject", "rva": 0x29abc, "kind": "public",
     "source": "public"},
    {"name": "?g_pTaskbar@@3PEAVCTaskbar@@EA", "rva": 0x23100, "kind": "global-data",
     "source": "global"},
    {"name": "?StaticHelper@`anonymous namespace'@@YAXXZ", "rva": 0x24321,
     "kind": "global-proc", "source": "global"},
]


def _req(symbol, **kw):
    req = {"symbol": symbol, "kind": kw.pop("kind", None), "module": "m.dll",
           "optional": kw.pop("optional", False), "alternatives": [symbol]}
    req.update(kw)
    return req


def test_exact_decorated_match():
    res = match.match_one("?Invoke@CTaskFrame@@UEAAJXZ", PDB_SYMBOLS)
    assert res["outcome"] == "found", res
    assert res["confidence"] == "exact", res
    assert res["rva"] == 0x20678, res
    assert res["matched_name"] == "?Invoke@CTaskFrame@@UEAAJXZ"


def test_structural_match_from_undecorated_request():
    res = match.match_one("protected: virtual __int64 __cdecl CTaskFrame::WndProc("
                          "struct HWND__ * __ptr64,unsigned int,unsigned __int64,long)",
                          PDB_SYMBOLS)
    assert res["outcome"] == "found", res
    assert res["confidence"] == "structural", res
    assert res["rva"] == 0x21100, res
    assert any("structurally" in n for n in res["notes"]), res["notes"]


def test_zero_argument_undecorated_match():
    res = match.match_one("public: virtual long __cdecl CTaskFrame::Invoke(void)",
                          PDB_SYMBOLS)
    assert res["outcome"] == "found" and res["confidence"] == "structural", res


def test_plain_export_match():
    res = match.match_one("DllGetClassObject", PDB_SYMBOLS)
    assert res["outcome"] == "found", res
    assert res["confidence"] == "exact", res


def test_data_symbol_match():
    res = match.match_one("?g_pTaskbar@@3PEAVCTaskbar@@EA", PDB_SYMBOLS)
    assert res["outcome"] == "found" and res["rva"] == 0x23100, res


def test_missing_symbol():
    res = match.match_one("?DoesNotExist@CTaskFrame@@UEAAJXZ", PDB_SYMBOLS)
    assert res["outcome"] == "missing", res
    assert res["matched_name"] is None
    assert any("PDB was read successfully" in n for n in res["notes"]), res["notes"]


def test_same_name_different_class_is_missing():
    res = match.match_one("public: void __cdecl Bar::foo(struct tagRECT *,int)",
                          [{"name": "?foo@Baz@@QEAAHH@Z", "rva": 1, "kind": "public"}])
    assert res["outcome"] == "missing", res


def test_overload_is_a_name_only_match():
    res = match.match_one("public: void __cdecl C::f(int,int)",
                          [{"name": "?f@C@@QEAAXH@Z", "rva": 1, "kind": "public"}])
    assert res["outcome"] == "found", res
    assert res["confidence"] == "partial", res
    assert any("different overload" in n for n in res["notes"]), res["notes"]


def test_case_only_difference_is_ambiguous():
    symbols = [{"name": "?Invoke@CTaskFrame@@UEAAJXZ", "rva": 1, "kind": "public"}]
    res = match.match_one("?invoke@ctaskframe@@ueaajxz", symbols)
    assert res["outcome"] == "ambiguous", res
    assert res["confidence"] == "weak", res
    assert any("NOT resolve at runtime" in n for n in res["notes"]), res["notes"]


def test_multiple_candidates_are_ambiguous():
    symbols = PDB_SYMBOLS + [{"name": "?Invoke@CTaskFrame@@UEAAJXZ", "rva": 0x30000,
                              "kind": "public", "source": "global"}]
    res = match.match_one("?Invoke@CTaskFrame@@UEAAJXZ", symbols)
    assert res["outcome"] == "ambiguous", res
    assert sorted(res["rvas"]) == [0x20678, 0x30000], res
    assert any("first exact match" in n for n in res["notes"]), res["notes"]


def test_no_symbols_is_unknown_not_missing():
    res = match.match_one("?Invoke@CTaskFrame@@UEAAJXZ", [])
    assert res["outcome"] == "unknown", res
    assert res["notes"], "unknown needs a reason"


def test_empty_request():
    res = match.match_one("", PDB_SYMBOLS)
    assert res["outcome"] == "missing" and "empty" in res["notes"][0], res


def test_wildcard_is_flagged_indicative():
    res = match.match_one("?Invoke@*", PDB_SYMBOLS)
    assert res["outcome"] == "found", res
    assert any("indicative" in n for n in res["notes"]), res["notes"]
    assert any("no wildcard matching" in n for n in res["notes"]), res["notes"]


def test_alternatives_stop_at_the_first_exact_match():
    req = _req("?Missing@CTaskFrame@@UEAAJXZ",
               alternatives=["?Missing@CTaskFrame@@UEAAJXZ",
                             "public: virtual long __cdecl CTaskFrame::Invoke(void)",
                             "?Invoke@CTaskFrame@@UEAAJXZ"])
    res = match.match_request(req, PDB_SYMBOLS)
    assert res["outcome"] == "found", res
    assert res["confidence"] == "exact", res
    assert res["requested"] == "?Invoke@CTaskFrame@@UEAAJXZ", res
    assert len(res["alternatives_tried"]) == 3, res["alternatives_tried"]


def test_optional_missing_is_annotated():
    req = _req("?Gone@C@@QEAAXXZ", optional=True)
    res = match.match_request(req, PDB_SYMBOLS)
    assert res["outcome"] == "missing", res
    assert any("optional" in n for n in res["notes"]), res["notes"]


def test_match_all_reports_unavailable_modules():
    reqs = [_req("?Invoke@CTaskFrame@@UEAAJXZ", module="twinui.pcshell.dll"),
            _req("?Invoke@CTaskFrame@@UEAAJXZ", module="taskbar.dll")]
    out = match.match_all(reqs, {"twinui.pcshell.dll": PDB_SYMBOLS})
    assert out[0]["result"]["outcome"] == "found", out[0]
    assert out[1]["result"]["outcome"] == "unknown", out[1]
    assert any("no PDB" in n for n in out[1]["result"]["notes"]), out[1]


def test_base_keys_prefilter_keeps_related_symbols():
    keys = match.base_keys("public: virtual long __cdecl CTaskFrame::Invoke(void)")
    assert "Invoke" in keys, keys
    pool = match._candidate_prefilter(PDB_SYMBOLS, keys)
    assert any(s["name"] == "?Invoke@CTaskFrame@@UEAAJXZ" for s in pool), pool
    assert not any(s["name"] == "DllGetClassObject" for s in pool), pool
    # an empty key list must not filter everything away
    assert match._candidate_prefilter(PDB_SYMBOLS, []) == PDB_SYMBOLS


def test_fixture_names_all_resolve_against_a_matching_table():
    """Every curated decorated name matches itself, byte for byte."""
    symbols = [{"name": name, "rva": 0x1000 + i, "kind": "public"}
               for i, (name, _q, _p, _c) in enumerate(R.DECORATED)]
    for name, _qualified, _params, _conf in R.DECORATED:
        res = match.match_one(name, symbols)
        assert res["outcome"] == "found" and res["confidence"] == "exact", (name, res)


def test_environment_description_is_honest():
    info = match.describe_environment()
    assert info["llvm_undname"] in (None,) or isinstance(info["llvm_undname"], str)
    assert "undecoration" in info and "pdb_analyzer" in info, info
    assert "exact strings" in info["wildcards"], info


def test_undname_helper_degrades_without_the_tool():
    if match.find_undname():
        return                      # a real llvm-undname would need real names
    assert match.undecorate_with_llvm(["?Invoke@CTaskFrame@@UEAAJXZ"]) is None
    assert match.undecorate_with_llvm([]) is None
