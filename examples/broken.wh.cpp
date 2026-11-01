// ==WindhawkMod==
// @id              broken
// @name            Broken
// @description     Deliberate compile error; tool must report structured build errors.
// @version         1.0.0
// @author          awt
// @include         *
// @architecture    x86-64
// ==/WindhawkMod==

#include <windhawk_api.h>

BOOL WINAPI Wh_ModInit() {
    this_function_does_not_exist(1, 2);
    return TRUE
}
