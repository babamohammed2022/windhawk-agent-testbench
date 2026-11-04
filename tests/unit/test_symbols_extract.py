"""Unit tests for `awt.symbols.extract`: what a mod asks Windows for.

The mod source below is synthetic (written for this test), but every shape in it
occurs in the published mods: a `SYMBOL_HOOK` table, one symbol name built from
several adjacent literals with a macro in the middle, an entry with alternative
names, an optional entry, a second and a third table for other modules, a
`Wh_SetFunctionHook` on a public API and one on a `GetProcAddress` result, an
x86 stdcall name, and a `#ifdef` whose condition this bench cannot evaluate.
"""
from awt.symbols import extract

MOD_SOURCE = r'''// ==WindhawkMod==
// @id           taskbar-clock-extras
// @name           Taskbar clock extras
// @version        1.2.3
// @target         explorer.exe
// @include        twinui.pcshell.dll
// @include        Taskbar.dll
// @architecture   x86-64
// ==/WindhawkMod==

#include <windhawk_api.h>

#ifdef MOD_FEATURE_X
#define EXTRA_SYMBOL L"?Extra@C@@QEAAHXZ"
#else
#define EXTRA_SYMBOL L"?Other@C@@QEAAHXZ"
#endif

#define THISCALL_STR L"__cdecl"

long ShowStartView_Hook(int a, int b);
long Invoke_Hook();
long CKbd_GetDeviceState_hook();

WindhawkUtils::SYMBOL_HOOK pcshellHooks[] = {
    {
        {
            L"public: virtual long "
            THISCALL_STR
            L" XamlLauncher::ShowStartView("
            L"enum IMMERSIVELAUNCHERSHOWMETHOD,"
            L"enum IMMERSIVELAUNCHERSHOWFLAGS)"
        },
        (void**)&ShowStartView_Original,
        ShowStartView_Hook,
    },
    {
        {
            L"?Invoke@CTaskFrame@@UEAAJXZ",
            L"public: virtual long __cdecl CTaskFrame::Invoke(void)",
        },
        &Invoke_Original,
        Invoke_Hook,
        /* optional */ true
    },
};

WindhawkUtils::SYMBOL_HOOK taskbarHooks[] = {
    {
        {L"?g_pTaskbar@@3PEAVCTaskbar@@EA"},
        &g_pTaskbar_Original,
        nullptr,
        false
    }
};

const WindhawkUtils::SYMBOL_HOOK dinput8DllHooks[] = {
    {
        {L"_CKbd_GetDeviceState@8"},
        &CKbd_GetDeviceState_orig,
        CKbd_GetDeviceState_hook,
        false
    }
};

BOOL Wh_ModInit() {
    HMODULE pcshell = LoadLibrary(L"twinui.pcshell.dll");
    WindhawkUtils::HookSymbols(pcshell, pcshellHooks, ARRAYSIZE(pcshellHooks));
    HMODULE taskbar = GetModuleHandle(L"Taskbar.dll");
    WindhawkUtils::HookSymbols(taskbar, taskbarHooks, ARRAYSIZE(taskbarHooks));
    WindhawkUtils::HookSymbols(GetModuleHandle(L"dinput8.dll"), dinput8DllHooks,
                               ARRAYSIZE(dinput8DllHooks));
    Wh_SetFunctionHook((void*)DrawTextW, (void*)DrawTextW_Hook,
                       (void**)&DrawTextW_Original);
    void* p = (void*)GetProcAddress(GetModuleHandle(L"dinput8.dll"),
                                    "DirectInput8Create");
    Wh_SetFunctionHook(p, (void*)DI8_Hook, (void**)&DI8_Original);
    return TRUE;
}
'''

CONCATENATED = ("public: virtual long __cdecl XamlLauncher::ShowStartView("
                "enum IMMERSIVELAUNCHERSHOWMETHOD,enum IMMERSIVELAUNCHERSHOWFLAGS)")


def _result(source=MOD_SOURCE, **kw):
    return extract.extract_requests(source, path="taskbar-clock-extras.wh.cpp", **kw)


def _by_symbol(result):
    return dict((r["symbol"], r) for r in result["requests"])


def test_metadata_and_modules():
    result = _result()
    assert result["mod_id"] == "taskbar-clock-extras", result["mod_id"]
    modules = result["modules"]
    loaded = [m["module"].lower() for m in modules["loaded"]]
    for expected in ("twinui.pcshell.dll", "taskbar.dll", "dinput8.dll"):
        assert expected in loaded, (expected, loaded)
    # @target is the process, @include lists the hooked modules: not the same
    assert modules["process_module"] == "explorer.exe", modules
    assert modules["include_modules"] == ["twinui.pcshell.dll", "Taskbar.dll"], modules
    assert modules["variable_map"].get("pcshell") == "twinui.pcshell.dll"
    assert modules["variable_map"].get("taskbar") == "Taskbar.dll"


def test_adjacent_literals_and_macros_are_joined():
    """One name written as four literals with a macro in the middle."""
    by_symbol = _by_symbol(_result())
    assert CONCATENATED in by_symbol, sorted(by_symbol)[:6]
    req = by_symbol[CONCATENATED]
    assert req["origin"] == "symbol_hook_table", req
    assert req["kind"] == "undecorated", req
    assert (req["module"] or "").lower() == "twinui.pcshell.dll", req
    assert req["module_basis"] == "variable", req
    assert req["line"] > 20, req
    # the pieces must not also be reported as separate symbols
    assert "public: virtual long " not in by_symbol


def test_alternative_names_and_optional_flag():
    result = _result()
    decorated = [r for r in result["requests"]
                 if r["symbol"] == "?Invoke@CTaskFrame@@UEAAJXZ"]
    undecorated = [r for r in result["requests"]
                   if r["symbol"].startswith(
                     "public: virtual long __cdecl CTaskFrame::Invoke")]
    assert decorated and undecorated, result["requests"]
    for req in decorated + undecorated:
        assert len(req["alternatives"]) == 2, req
        assert req["optional"] is True, "the /* optional */ true flag was lost"
        assert req["origin"] == "symbol_hook_table", req


def test_each_table_gets_its_own_module():
    by_symbol = _by_symbol(_result())
    taskbar = by_symbol.get("?g_pTaskbar@@3PEAVCTaskbar@@EA")
    assert taskbar, sorted(by_symbol)
    assert (taskbar["module"] or "").lower() == "taskbar.dll", taskbar
    assert taskbar["module_basis"] == "variable", taskbar
    assert taskbar["kind"] == "decorated", taskbar
    assert taskbar["optional"] is False, taskbar


def test_inline_module_literal():
    """`HookSymbols(GetModuleHandle(L"dinput8.dll"), table, n)`."""
    by_symbol = _by_symbol(_result())
    x86 = by_symbol.get("_CKbd_GetDeviceState@8")
    assert x86, sorted(by_symbol)
    assert x86["kind"] == "decorated", x86
    assert (x86["module"] or "").lower() == "dinput8.dll", x86
    assert x86["module_basis"] == "inline-literal", x86


def test_public_api_hooks():
    api = dict((r["symbol"], r) for r in _result()["requests"]
               if r["origin"] == "public_api_hook")
    assert "DrawTextW" in api, sorted(api)
    assert (api["DrawTextW"]["module"] or "").lower() == "user32.dll", api["DrawTextW"]
    assert api["DrawTextW"]["module_basis"] == "api-table", api["DrawTextW"]
    # `p = GetProcAddress(GetModuleHandle(L"dinput8.dll"), "DirectInput8Create")`
    # has to be traced through the variable, module included.
    assert "DirectInput8Create" in api, sorted(api)
    assert (api["DirectInput8Create"]["module"] or "").lower() == "dinput8.dll", \
        api["DirectInput8Create"]
    assert api["DirectInput8Create"]["module_basis"] == "getprocaddress-variable"
    assert "p" not in api, "a bare variable name is not a symbol"


def test_untraceable_hook_target_is_reported_not_guessed():
    source = MOD_SOURCE + '''
void* ResolveLater();
void g() { Wh_SetFunctionHook(ResolveLater(), (void*)H, (void**)&O); }
'''
    result = _result(source)
    assert result["unresolved_hooks"], "an untraceable hook target was dropped silently"
    entry = result["unresolved_hooks"][0]
    assert entry["expression"].startswith("ResolveLater"), entry
    assert entry["reason"] and entry["line"] > 0, entry
    # and no invented symbol request for it
    assert "ResolveLater" not in _by_symbol(result), _by_symbol(result).keys()


def test_unevaluable_condition_keeps_both_branches():
    """A mod-specific `#ifdef` cannot be evaluated: nothing may be dropped."""
    result = _result()
    notes = " ".join(result["preprocessor_notes"])
    assert "MOD_FEATURE_X" in notes, result["preprocessor_notes"]
    assert "both branches kept" in notes, notes
    symbols = {r["symbol"] for r in result["requests"]}
    assert "?Extra@C@@QEAAHXZ" in symbols, sorted(symbols)
    assert "?Other@C@@QEAAHXZ" in symbols, sorted(symbols)


def test_known_architecture_condition_is_resolved():
    source = MOD_SOURCE.replace("#ifdef MOD_FEATURE_X", "#ifdef _M_IX86")
    symbols = {r["symbol"] for r in _result(source)["requests"]}
    assert "?Other@C@@QEAAHXZ" in symbols, sorted(symbols)
    assert "?Extra@C@@QEAAHXZ" not in symbols, \
        "the x86-only branch was kept although _M_IX86 is undefined for x86-64"


def test_comments_are_not_symbols():
    source = '''
// WindhawkUtils::SYMBOL_HOOK disabled[] = { { {L"?Nope@C@@QEAAXXZ"} } };
/* L"?AlsoNope@C@@QEAAXXZ" */
void f() {
    const wchar_t* message = L"hook failed";
    WindhawkUtils::SYMBOL_HOOK real[] = { { {L"?Real@C@@QEAAXXZ"}, &o, h, false } };
    HookSymbols(GetModuleHandle(L"twinui.pcshell.dll"), real, 1);
}
'''
    symbols = {r["symbol"] for r in _result(source)["requests"]}
    assert "?Real@C@@QEAAXXZ" in symbols, sorted(symbols)
    for absent in ("?Nope@C@@QEAAXXZ", "?AlsoNope@C@@QEAAXXZ"):
        assert absent not in symbols, "%s came from a comment" % absent


def test_no_duplicate_requests():
    seen = set()
    for req in _result()["requests"]:
        key = (req["symbol"], req["origin"], req["line"], req["module"])
        assert key not in seen, "duplicate request %r" % (key,)
        seen.add(key)


def test_empty_and_odd_sources_do_not_crash():
    for source in ("", "// nothing here", "#include <windhawk_api.h>",
                   "void f() { WindhawkUtils::SYMBOL_HOOK h[] = {",
                   'L"unterminated', "WindhawkUtils::SYMBOL_HOOK h[] = "
                                     "{ { {L\"?a@B@@QEAAXXZ\"}, 0, 0 } };"):
        result = extract.extract_requests(source, path="odd.wh.cpp")
        assert isinstance(result["requests"], list)
        assert result["modules"] is not None


def test_candidate_classification():
    assert extract._candidate_is_symbol("?Invoke@CTaskFrame@@UEAAJXZ") == "decorated"
    assert extract._candidate_is_symbol(
        "public: virtual long __cdecl C::f(int)") == "undecorated"
    assert extract._candidate_is_symbol(
        "public: virtual int __cdecl winrt::impl::produce<A,B>::MeasureOverride("
        "struct winrt::Windows::Foundation::Size)") == "undecorated"
    assert extract._candidate_is_symbol("_CKbd_GetDeviceState@8") == "decorated"
    assert extract._candidate_is_symbol("@Init@12") == "decorated"
    assert extract._candidate_is_symbol("DrawTextW") == "plain"
    assert extract._candidate_is_symbol("__ImageBase") is None
    assert extract._candidate_is_symbol("hello world") is None
    assert extract._candidate_is_symbol("C:\\Windows\\explorer.exe") is None
    assert extract._candidate_is_symbol("") is None


def test_module_name_validation():
    """An error message is not a module name."""
    source = '''
// ==WindhawkMod==
// @id           m
// @target       explorer.exe
// ==/WindhawkMod==
void f() {
    if (!LoadLibrary(L"twinui.pcshell.dll")) {
        MessageBox(nullptr, L"Failed to load twinui.pcshell.dll", L"x", 0);
    }
    WindhawkUtils::SYMBOL_HOOK h[] = { { {L"?a@B@@QEAAXXZ"}, &o, hk, false } };
    WindhawkUtils::HookSymbols(GetModuleHandle(L"twinui.pcshell.dll"), h, 1);
}
'''
    result = _result(source)
    for req in result["requests"]:
        if req["origin"] == "symbol_hook_table":
            assert (req["module"] or "").lower() == "twinui.pcshell.dll", req
            assert "Failed to load" not in (req["module"] or ""), req


def test_scan_masks_literal_text_but_keeps_offsets():
    source = 'int a = 1;\nconst wchar_t* s = L"?x@C@@QEAAXXZ";\nint b = 2;'
    code, literals = extract.scan(source)
    assert len(code) == len(source), (len(code), len(source))
    assert code.count("\n") == source.count("\n")
    assert "?x@C@@" not in code, "literal text leaked into the masked code"
    assert len(literals) == 1
    start, end, kind, text, line = literals[0]
    assert source[start:end].startswith('L"') and source[start:end].endswith('"')
    assert text == "?x@C@@QEAAXXZ" and kind == "wide" and line == 2


def test_merge_adjacent_literals_needs_only_whitespace():
    # C++ concatenates adjacent literals even across newlines, so the two parts
    # of the first statement are one name; a separator keeps the second pair apart.
    source = 'const wchar_t* a = L"abc"\n    L"def";\nconst wchar_t* b = L"x" + L"y";'
    code, literals = extract.scan(source)
    merged = extract.merge_adjacent_literals(literals, code)
    texts = [m[3] for m in merged]
    assert "abcdef" in texts, texts
    assert "x" in texts and "y" in texts, texts
    assert "xy" not in texts, "literals separated by '+' are not concatenated"


def test_merge_across_a_macro_expansion():
    source = ('#define THISCALL_STR L"__cdecl"\n'
              'const wchar_t* a = L"public: virtual long "\n'
              '    THISCALL_STR\n'
              '    L" C::f(void)";')
    code, literals = extract.scan(source)
    macros = extract.collect_macro_literals(source)
    assert macros.get("THISCALL_STR") == "__cdecl", macros
    texts = [m[3] for m in extract.merge_adjacent_literals(literals, code, macros)]
    assert "public: virtual long __cdecl C::f(void)" in texts, texts
