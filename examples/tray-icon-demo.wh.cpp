// ==WindhawkMod==
// @id              tray-icon-demo
// @name            Tray Icon Demo
// @description     Hooks Shell_NotifyIconW and logs tray icon operations.
// @version         1.0.0
// @author          awt
// @include         explorer.exe
// @architecture    x86-64
// ==/WindhawkMod==

#include <windhawk_api.h>
#include <windhawk_utils.h>
#include <shellapi.h>

using Shell_NotifyIconW_t = decltype(&Shell_NotifyIconW);
Shell_NotifyIconW_t Shell_NotifyIconW_Original;

BOOL WINAPI Shell_NotifyIconW_Hook(DWORD message, NOTIFYICONDATAW* data) {
    Wh_Log(L"Shell_NotifyIconW: msg=%u tip=%s", message,
           (data && (data->uFlags & NIF_TIP)) ? data->szTip : L"-");
    return Shell_NotifyIconW_Original(message, data);
}

BOOL WINAPI Wh_ModInit() {
    Wh_Log(L"tray demo init");
    return WindhawkUtils::SetFunctionHook(Shell_NotifyIconW, Shell_NotifyIconW_Hook,
                                          &Shell_NotifyIconW_Original);
}
