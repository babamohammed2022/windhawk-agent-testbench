// ==WindhawkMod==
// @id              conf-init-false
// @name            Conformance init false
// @description     Wh_ModInit returns FALSE: loading must abort cleanly.
// @version         1.0.0
// @author          awt
// @include         testapp.exe
// @architecture    x86-64
// ==/WindhawkMod==

#include <windhawk_api.h>

BOOL WINAPI Wh_ModInit() {
    Wh_Log(L"CONF2 refusing to load");
    return FALSE;
}
