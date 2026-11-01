// ==WindhawkMod==
// @id              control-panel-demo
// @name            Control Panel Demo
// @description     Targets control.exe; sandbox probes Wine's control.exe and falls back honestly.
// @version         1.0.0
// @author          awt
// @include         control.exe
// @architecture    x86-64
// ==/WindhawkMod==

#include <windhawk_api.h>

BOOL WINAPI Wh_ModInit() {
    Wh_Log(L"control panel demo init");
    return TRUE;
}
