"""Unit tests for `awt.symbols.demangle`.

The names and the expected decodings come from `fixtures/real_symbols.py`, which
records where they were observed. Nothing here needs a PDB: the demangler is the
piece that decides whether a name a mod wrote and a name Microsoft's PDB
contains are the same symbol.
"""
from awt.symbols import demangle
from tests.unit.fixtures import real_symbols as R


def test_is_decorated_and_looks_undecorated():
    assert demangle.is_decorated("?Invoke@CTaskFrame@@UEAAJXZ")
    assert demangle.is_decorated("??0AppMenu@@QEAA@XZ")
    assert not demangle.is_decorated("DrawTextW")
    assert not demangle.is_decorated("_CKbd_GetDeviceState@8")
    assert not demangle.is_decorated("? ms-resource:///ActionCenter")
    assert demangle.looks_undecorated("public: virtual long __cdecl C::f(void)")
    assert demangle.looks_undecorated("int __cdecl wWinMain(struct HINSTANCE__ *,int)")
    assert not demangle.looks_undecorated("DrawTextW")


def test_decorated_names():
    for name, qualified, params, confidence in R.DECORATED:
        parsed = demangle.parse_decorated(name)
        assert parsed["qualified_name"] == qualified, (name, parsed["qualified_name"])
        assert parsed["param_count"] == params, (name, parsed["param_count"])
        assert parsed["confidence"] == confidence, (name, parsed["confidence"])


def test_scope_is_reversed_to_natural_order():
    """MSVC writes the innermost identifier first."""
    parsed = demangle.parse_decorated("?ShowStartView@XamlLauncher@@UEAAJXZ")
    assert parsed["scope_raw"][0] == "ShowStartView", parsed["scope_raw"]
    assert parsed["scope"] == ["XamlLauncher", "ShowStartView"], parsed["scope"]
    assert parsed["qualified_name"] == "XamlLauncher::ShowStartView"


def test_access_static_virtual():
    cases = {
        "?Invoke@CTaskFrame@@UEAAJXZ": ("public", False, True),
        "?WndProc@CTaskFrame@@MEAA_JPEAUHWND__@@I_K_J@Z": ("protected", False, True),
        "?Create@Factory@@SA?AVComPtr@IUnknown@@XZ": ("public", True, False),
        "?foo@Bar@@QEAAHPEAUtagRECT@@H@Z": ("public", False, False),
        # a global function has no access specifier; the decoder says "global"
        "?wWinMain@@YAHPEAUHINSTANCE__@@HPEADH@Z": ("global", False, False),
    }
    for name, (access, is_static, is_virtual) in cases.items():
        parsed = demangle.parse_decorated(name)
        assert parsed["access"] == access, (name, parsed["access"])
        assert bool(parsed["is_static"]) == is_static, (name, parsed["is_static"])
        assert bool(parsed["is_virtual"]) == is_virtual, (name, parsed["is_virtual"])


def test_zero_argument_functions_have_zero_parameters():
    """`XZ` is an empty parameter list; DIA prints such a function as `f(void)`."""
    for name in ("?Invoke@CTaskFrame@@UEAAJXZ",
                 "?StaticHelper@`anonymous namespace'@@YAXXZ",
                 "?Create@Factory@@SA?AVComPtr@IUnknown@@XZ"):
        assert demangle.parse_decorated(name)["param_count"] == 0, name


def test_constructors_and_destructors():
    for name, qualified, params in R.CONSTRUCTORS:
        parsed = demangle.parse_decorated(name)
        assert parsed["qualified_name"] == qualified, (name, parsed)
        assert parsed["param_count"] == params, (name, parsed)
        assert parsed["kind"] == "constructor", (name, parsed["kind"])
    parsed = demangle.parse_decorated("??1CTrayNotify@@UEAA@XZ")
    assert parsed["qualified_name"] == "CTrayNotify::~CTrayNotify", parsed
    assert parsed["kind"] == "destructor", parsed


def test_deleting_destructors_have_a_return_type():
    for name, qualified, params in R.DELETING_DESTRUCTORS:
        parsed = demangle.parse_decorated(name)
        assert parsed["qualified_name"] == qualified, (name, parsed)
        assert parsed["param_count"] == params, (name, parsed)


def test_data_symbols():
    for name, qualified, kind in R.DATA_SYMBOLS:
        parsed = demangle.parse_decorated(name)
        assert parsed["kind"] == kind, (name, parsed["kind"])
        assert parsed["qualified_name"] == qualified, (name, parsed)
        assert parsed["param_count"] is None, (name, parsed["param_count"])


def test_templates():
    parsed = demangle.parse_decorated(
        "?assign@?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@"
        "QEAAAEAV12@PEBDH@Z")
    assert parsed["scope"] == ["std", "basic_string", "assign"], parsed["scope"]
    assert parsed["template"] is True, parsed
    assert parsed["param_count"] == 2, parsed


def test_undecorated_parsing():
    parsed = demangle.parse_undecorated(
        "public: virtual long __cdecl CTaskFrame::Invoke(void)")
    assert parsed["scope"] == ["CTaskFrame", "Invoke"], parsed
    assert parsed["access"] == "public" and parsed["is_virtual"] is True
    assert parsed["param_count"] == 0, parsed
    parsed = demangle.parse_undecorated(
        "int __cdecl wWinMain(struct HINSTANCE__ * __ptr64,int,char * __ptr64,int)")
    assert parsed["scope"] == ["wWinMain"], parsed
    assert parsed["param_count"] == 4, parsed


def test_undecorated_pairs_match():
    for undecorated, decorated in R.UNDDECORATED_PAIRS:
        if not decorated:
            continue
        result = demangle.compare(undecorated, decorated)
        assert result["matched"] is True, (undecorated, result)
        assert result["confidence"] in ("exact", "structural"), result


def test_non_matching_pairs():
    for undecorated, decorated in R.NON_MATCHING:
        result = demangle.compare(undecorated, decorated)
        assert not (result["matched"] and result["confidence"] in ("exact", "structural")), \
            (undecorated, decorated, result)


def test_overloads_are_partial_not_missing():
    for undecorated, decorated in R.OVERLOAD_PAIRS:
        result = demangle.compare(undecorated, decorated)
        assert result["matched"] is True, result
        assert result["confidence"] == "partial", result
        assert any("parameter count differs" in n for n in result["notes"]), result


def test_partial_encodings_degrade_safely():
    for name, qualified in R.PARTIAL_DECORATED:
        parsed = demangle.parse_decorated(name)
        assert parsed["qualified_name"] == qualified, (name, parsed)
        assert parsed["confidence"] == "partial", parsed
        assert parsed["notes"], "a partial decode must say why"


def test_byte_identical_names_are_exact():
    name = "?Invoke@CTaskFrame@@UEAAJXZ"
    result = demangle.compare(name, name)
    assert result["confidence"] == "exact" and result["score"] == 1000, result


def test_case_only_difference_is_weak():
    result = demangle.compare("?invoke@ctaskframe@@ueaajxz",
                              "?Invoke@CTaskFrame@@UEAAJXZ")
    assert result["confidence"] == "weak", result
    assert result["notes"] and "NOT match at runtime" in result["notes"][0], result


def test_garbage_does_not_crash():
    for name in ("", "?", "?@", "??", "not a symbol", "?" * 40, "?a@B@@ZZZZ",
                 "?x@?$@$@$@@UEAAAXXZ", "L\"?Invoke@C@@QEAAXXZ\""):
        parsed = demangle.parse_decorated(name)
        assert parsed["confidence"] in ("weak", "partial", "structural"), parsed
        result = demangle.compare(name, "?Invoke@CTaskFrame@@UEAAJXZ")
        assert isinstance(result, dict) and "outcome" not in result


def test_x86_decorations():
    parsed = demangle.parse_decorated("?Invoke@CTaskFrame@@UAGJXZ", arch="x86")
    assert parsed["qualified_name"] == "CTaskFrame::Invoke", parsed
    assert parsed["confidence"] in ("structural", "partial"), parsed
