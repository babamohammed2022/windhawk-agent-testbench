// ==WindhawkMod==
// @id              conf-log
// @name            Conformance log formats
// @description     Wh_Log printf rules with wide strings.
// @version         1.0.0
// @author          awt
// @include         testapp.exe
// @architecture    x86-64
// ==/WindhawkMod==

#include <windhawk_api.h>

BOOL WINAPI Wh_ModInit() {
    Wh_Log(L"CONF4 int=%d hex=%x str=%s narrow=%S", 42, 255, L"wide", "narrow");
    return TRUE;
}
