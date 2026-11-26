// ==WindhawkMod==
// @id              symbol-lookup-demo
// @name            Symbol lookup demo
// @description     Uses the public Wh_FindFirstSymbol API; resolution is not verifiable under Wine.
// @version         1.0.0
// @author          awt
// @include         testapp.exe
// @architecture    x86-64
// ==/WindhawkMod==

#include <windhawk_api.h>

// kernel32 is only used as a stable module handle. The bench's shim refuses
// symbol lookup on purpose: an empty table here must never be read as "the
// symbol does not exist on Windows". See python3 -m awt.symbols for names.
BOOL WINAPI Wh_ModInit() {
    HMODULE module = GetModuleHandleW(L"kernel32.dll");
    WH_FIND_SYMBOL_OPTIONS options = {sizeof(options), nullptr, FALSE};
    WH_FIND_SYMBOL symbol = {};
    HANDLE search = Wh_FindFirstSymbol(module, &options, &symbol);
    if (!search) {
        Wh_Log(L"symbol lookup unavailable in this sandbox (expected)");
        return TRUE;
    }
    Wh_Log(L"symbol: %s", symbol.symbol ? symbol.symbol : L"(none)");
    Wh_FindCloseSymbol(search);
    return TRUE;
}
