// ==WindhawkMod==
// @id              crash-demo
// @name            Crash Demo
// @description     Deliberately dereferences NULL in Wh_ModInit; tool must report crashed.
// @version         1.0.0
// @author          awt
// @include         testapp.exe
// @architecture    x86-64
// ==/WindhawkMod==

#include <windhawk_api.h>

BOOL WINAPI Wh_ModInit() {
    Wh_Log(L"about to crash");
    volatile int* p = nullptr;
    *p = 42;
    return TRUE;
}
