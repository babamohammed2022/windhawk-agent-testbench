// ==WindhawkMod==
// @id          awt-prgate-demo
// @name        AWT PR Gate Demo Mod
// @description A small mod used to exercise the official PR validation replay.
// @version     1.0
// @author      AWT PR Gate Fixture
// @github      https://github.com/awt-prgate-fixture
// @include     notepad.exe
// @twitter     https://x.com/awt-prgate-fixture
// @license     MIT
// ==/WindhawkMod==

// ==WindhawkModReadme==
/*
A mod used by the testbench's own tests. It logs a line at init and uninit.
*/
// ==/WindhawkModReadme==

#include <windhawk_api.h>

BOOL Wh_ModInit() {
    Wh_Log(L"AWT PR gate demo initialized");
    return TRUE;
}

void Wh_ModUninit() {
    Wh_Log(L"AWT PR gate demo uninitialized");
}
