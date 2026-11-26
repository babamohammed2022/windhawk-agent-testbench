// ==WindhawkMod==
// @id              tool-mod-demo
// @name            Tool mod demo
// @description     Illustrates the tool-mod surface. The bench has no dedicated tool process; see the report.
// @version         1.0.0
// @author          awt
// @include         *
// @architecture    x86-64
// ==/WindhawkMod==

#include <windhawk_api.h>

// A real tool mod is initialised by Windhawk in a dedicated tool process.
// WH_TOOL_MOD is not part of the cached 1.7.3 mod header, so the guard keeps
// this example compilable while the static check and capability matrix mark
// the tool-mod behaviour not_verifiable.
BOOL WINAPI Wh_ModInit() {
#ifdef WH_TOOL_MOD
    Wh_Log(L"tool mod initialised in a dedicated process (not available here)");
#else
    Wh_Log(L"tool mod demo: no dedicated tool process in this bench");
#endif
    return TRUE;
}
