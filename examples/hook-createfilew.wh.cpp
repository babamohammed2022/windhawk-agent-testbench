// ==WindhawkMod==
// @id              hook-createfilew
// @name            Hook CreateFileW
// @description     Logs every CreateFileW call through a real inline hook (MinHook).
// @version         1.0.0
// @author          awt
// @include         *
// @architecture    x86-64
// ==/WindhawkMod==

#include <windhawk_api.h>
#include <windhawk_utils.h>

using CreateFileW_t = decltype(&CreateFileW);
CreateFileW_t CreateFileW_Original;

HANDLE WINAPI CreateFileW_Hook(LPCWSTR path, DWORD access, DWORD share,
                               LPSECURITY_ATTRIBUTES sa, DWORD disp,
                               DWORD flags, HANDLE tmpl) {
    Wh_Log(L"CreateFileW: %s", path);
    return CreateFileW_Original(path, access, share, sa, disp, flags, tmpl);
}

BOOL WINAPI Wh_ModInit() {
    Wh_Log(L"Init: hooking CreateFileW");
    if (!WindhawkUtils::SetFunctionHook(CreateFileW, CreateFileW_Hook, &CreateFileW_Original)) {
        Wh_Log(L"SetFunctionHook failed");
        return FALSE;
    }
    return TRUE;
}
