// ==WindhawkMod==
// @id              hello-log
// @name            Hello Log
// @description     Logs a greeting at init; the smallest possible mod.
// @version         1.0.0
// @author          awt
// @include         explorer.exe
// @architecture    x86-64
// ==/WindhawkMod==

// ==WindhawkModSettings==
/*
- enabled: true
  $name: Enabled
*/
// ==/WindhawkModSettings==

#include <windhawk_api.h>

BOOL WINAPI Wh_ModInit() {
    Wh_Log(L"Hello from the sandbox, mod id %s", WH_MOD_ID);
    Wh_Log(L"enabled setting = %d", Wh_GetIntSetting(L"enabled"));
    return TRUE;
}

void WINAPI Wh_ModUninit() {
    Wh_Log(L"Bye from the sandbox");
}
