// ==WindhawkMod==
// @id              visual-playground
// @name            Visual playground accent demo
// @description     Adds a visible accent to the playground's GDI heading through a real API hook.
// @version         1.0.0
// @include         awt-playground.exe
// @architecture    x86-64
// ==/WindhawkMod==

// ==WindhawkModSettings==
/*
- enabled: true
  $name: Enable visible accent
*/
// ==/WindhawkModSettings==

#include <windhawk_api.h>
#include <windhawk_utils.h>
#include <cwchar>

static decltype(&DrawTextW) DrawTextW_Original;
static bool g_enabled;

int WINAPI DrawTextW_Hook(HDC dc, LPCWSTR text, int length, LPRECT rect, UINT format) {
    // Target ONLY the stable heading in our specimen, not arbitrary caller buffers.
    if (!g_enabled || !text || length != -1 || !rect ||
        wcscmp(text, L"Windhawk visual playground") != 0) {
        return DrawTextW_Original(dc, text, length, rect, format);
    }
    HBRUSH brush = CreateSolidBrush(RGB(24, 95, 160));
    if (brush) { FillRect(dc, rect, brush); DeleteObject(brush); }
    COLORREF old = SetTextColor(dc, RGB(255, 255, 255));
    int result = DrawTextW_Original(dc, L"Mod applied: visible accent", -1, rect, format);
    SetTextColor(dc, old);
    Wh_Log(L"DrawTextW: playground accent drawn");
    return result;
}

BOOL Wh_ModInit() {
    g_enabled = Wh_GetIntSetting(L"enabled") != 0;
    return WindhawkUtils::SetFunctionHook(DrawTextW, DrawTextW_Hook, &DrawTextW_Original);
}
