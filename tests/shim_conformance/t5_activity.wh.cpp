// ==WindhawkMod==
// @id              conformance-activity
// @name            Host activity regression fixture
// @version         1.0.0
// @include         *
// @architecture    x86-64
// ==/WindhawkMod==

#include <windhawk_api.h>
#include <windhawk_utils.h>

static decltype(&ReadFile) ReadFile_Original;
static decltype(&GetWindowRect) GetWindowRect_Original;

BOOL WINAPI ReadFile_Hook(HANDLE file, LPVOID buffer, DWORD bytes,
                          LPDWORD read, LPOVERLAPPED overlapped) {
    Wh_Log(L"ReadFile: called");
    return ReadFile_Original(file, buffer, bytes, read, overlapped);
}

BOOL WINAPI GetWindowRect_Hook(HWND window, LPRECT rect) {
    Wh_Log(L"GetWindowRect: called");
    return GetWindowRect_Original(window, rect);
}

BOOL Wh_ModInit() {
    return WindhawkUtils::SetFunctionHook(ReadFile, ReadFile_Hook, &ReadFile_Original) &&
           WindhawkUtils::SetFunctionHook(GetWindowRect, GetWindowRect_Hook, &GetWindowRect_Original);
}

void Wh_ModAfterInit() {
    Wh_Log(L"ACTIVITY after init");
}

void Wh_ModSettingsChanged() {
    Wh_Log(L"ACTIVITY settings notification");
}
