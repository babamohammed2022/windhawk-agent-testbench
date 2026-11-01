// ==WindhawkMod==
// @id              conf-strings
// @name            Conformance strings
// @description     Wh_GetStringSetting memory + indexed names + free.
// @version         1.0.0
// @author          awt
// @include         testapp.exe
// @architecture    x86-64
// ==/WindhawkMod==

// ==WindhawkModSettings==
/*
- greeting: "hello sandbox"
  $name: Greeting
*/
// ==/WindhawkModSettings==

#include <windhawk_api.h>

BOOL WINAPI Wh_ModInit() {
    PCWSTR g = Wh_GetStringSetting(L"greeting");
    Wh_Log(L"CONF1 greeting=%s", g);
    Wh_FreeStringSetting(g);
    Wh_Log(L"CONF1 indexed=%d", Wh_GetIntSetting(L"items[%d].size", 3));
    return TRUE;
}
