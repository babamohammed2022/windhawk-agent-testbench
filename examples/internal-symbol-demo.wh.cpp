// ==WindhawkMod==
// @id              internal-symbol-demo
// @name            Internal Symbol Demo
// @description     Hooks an internal explorer.exe symbol; MUST report unverifiable.
// @version         1.0.0
// @author          awt
// @include         explorer.exe
// @architecture    x86-64
// ==/WindhawkMod==

#include <windhawk_api.h>
#include <windhawk_utils.h>

using ShowStartView_t = HRESULT(WINAPI*)(void*, int, int);
ShowStartView_t ShowStartView_Original;

HRESULT WINAPI ShowStartView_Hook(void* pThis, int method, int flags) {
    Wh_Log(L"ShowStartView intercepted");
    return ShowStartView_Original(pThis, method, flags);
}

BOOL WINAPI Wh_ModInit() {
    HMODULE twinui = GetModuleHandleW(L"twinui.pcshell.dll");
    if (!twinui) twinui = LoadLibraryExW(L"twinui.pcshell.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!twinui) {
        Wh_Log(L"twinui.pcshell.dll not present (expected outside Windows)");
        return TRUE;
    }
    const WindhawkUtils::SYMBOL_HOOK hooks[] = {
        { { LR"(public: virtual long __cdecl XamlLauncher::ShowStartView(enum IMMERSIVELAUNCHERSHOWMETHOD,enum IMMERSIVELAUNCHERSHOWFLAGS))" },
          &ShowStartView_Original, ShowStartView_Hook },
    };
    if (!WindhawkUtils::HookSymbols(twinui, hooks, ARRAYSIZE(hooks))) {
        Wh_Log(L"HookSymbols failed; needs real Windows");
    }
    return TRUE;
}
