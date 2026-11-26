// ==WindhawkMod==
// @id              settings-array-demo
// @name            Complex settings demo
// @name:it-IT      Demo impostazioni complesse
// @description     Reads an array of settings and a quoted string; exercises @compilerOptions.
// @version         1.0.0
// @author          awt
// @include         testapp.exe
// @compilerOptions -lcomctl32 -DFIDELITY_ARRAY=1
// @architecture    x86-64
// @license         MIT
// ==/WindhawkMod==

// ==WindhawkModSettings==
/*
- items:
  - name: "first"
    size: 4
  - name: "second"
    size: 7
- greeting: "hello sandbox"
  $name: Greeting
*/
// ==/WindhawkModSettings==

#include <windhawk_api.h>
#include <windhawk_utils.h>

// The array keys are flattened by the bench into `items[0].name` etc. The
// setting values are emulated (simulated), not read from the Windhawk UI.
BOOL WINAPI Wh_ModInit() {
    auto first = WindhawkUtils::StringSetting::make(L"items[%d].name", 0);
    auto greeting = WindhawkUtils::StringSetting::make(L"greeting");
    Wh_Log(L"complex: first=%s size1=%d greeting=%s flag=%d", first.get(),
           Wh_GetIntSetting(L"items[%d].size", 1), greeting.get(), FIDELITY_ARRAY);
    return TRUE;
}
