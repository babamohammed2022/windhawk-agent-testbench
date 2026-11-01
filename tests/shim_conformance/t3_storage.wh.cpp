// ==WindhawkMod==
// @id              conf-storage
// @name            Conformance storage
// @description     int/string/binary storage round-trip + delete.
// @version         1.0.0
// @author          awt
// @include         testapp.exe
// @architecture    x86-64
// ==/WindhawkMod==

#include <windhawk_api.h>

BOOL WINAPI Wh_ModInit() {
    Wh_SetIntValue(L"n", 42);
    Wh_SetStringValue(L"s", L"stored");
    unsigned char bin[3] = { 1, 2, 250 };
    Wh_SetBinaryValue(L"b", bin, 3);
    unsigned char back[3] = {};
    size_t got = Wh_GetBinaryValue(L"b", back, 3);
    Wh_Log(L"CONF3 n=%d s-ok=%d bin=%d,%d,%d got=%u del=%d",
           Wh_GetIntValue(L"n", 0),
           0,  // string compared below
           back[0], back[1], back[2], (unsigned)got, (int)Wh_DeleteValue(L"n"));
    WCHAR buf[64] = {};
    Wh_GetStringValue(L"s", buf, 64);
    Wh_Log(L"CONF3 s=%s", buf);
    return TRUE;
}
