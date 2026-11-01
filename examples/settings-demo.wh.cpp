// ==WindhawkMod==
// @id              settings-demo
// @name            Settings Demo
// @description     Reads every kind of setting declared in the YAML block.
// @version         1.0.0
// @author          awt
// @include         testapp.exe
// @architecture    x86-64
// ==/WindhawkMod==

// ==WindhawkModSettings==
/*
- enabled: true
  $name: Enabled
- greeting: "hello sandbox"
  $name: Greeting
- retries: 3
  $name: Retries
*/
// ==/WindhawkModSettings==

#include <windhawk_api.h>

BOOL WINAPI Wh_ModInit() {
    Wh_Log(L"enabled=%d retries=%d", Wh_GetIntSetting(L"enabled"), Wh_GetIntSetting(L"retries"));
    PCWSTR g = Wh_GetStringSetting(L"greeting");
    Wh_Log(L"greeting=%s", g);
    Wh_FreeStringSetting(g);
    // indexed names, printf-style (documented behaviour)
    Wh_Log(L"indexed missing item=%d", Wh_GetIntSetting(L"items[%d].size", 2));
    return TRUE;
}
