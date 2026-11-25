// A real Win32 specimen, NOT Explorer or a replacement for the Windows shell.
// Capture the client pixels after paint + a fixed settling interval; no sleep in WM_PAINT.
#include <windows.h>
#include <commctrl.h>
#include <cstdio>
#include <cwchar>
#include <vector>

static const wchar_t* g_capture = nullptr;
static int g_result = 1;
static ULONGLONG g_started = 0;
static int g_settle = 800;
static unsigned g_paints = 0;

static void Mark(const char* text) {
    printf("[awt-preview] %s\n", text);
    fflush(stdout);
}

// Top-down, uncompressed BGRA BMP. The Python side validates and converts it to PNG.
static bool Capture(HWND window) {
    RECT rect = {};
    if (!GetClientRect(window, &rect) || rect.right <= 0 || rect.bottom <= 0 ||
        rect.right > 2048 || rect.bottom > 1536) return false;
    HDC screen = GetDC(window);
    if (!screen) return false;
    HDC memory = CreateCompatibleDC(screen);
    BITMAPINFO info = {};
    info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    info.bmiHeader.biWidth = rect.right;
    info.bmiHeader.biHeight = -rect.bottom;
    info.bmiHeader.biPlanes = 1;
    info.bmiHeader.biBitCount = 32;
    info.bmiHeader.biCompression = BI_RGB;
    DWORD bytes = (DWORD)rect.right * (DWORD)rect.bottom * 4;
    info.bmiHeader.biSizeImage = bytes;
    void* pixels = nullptr;
    HBITMAP bitmap = memory ? CreateDIBSection(screen, &info, DIB_RGB_COLORS,
                                               &pixels, nullptr, 0) : nullptr;
    bool ok = false;
    if (bitmap) {
        HGDIOBJ old = SelectObject(memory, bitmap);
        GdiFlush();
        if (BitBlt(memory, 0, 0, rect.right, rect.bottom, screen, 0, 0, SRCCOPY)) {
            GdiFlush();
            BITMAPFILEHEADER header = {};
            header.bfType = 0x4d42;
            header.bfOffBits = sizeof(header) + sizeof(BITMAPINFOHEADER);
            header.bfSize = header.bfOffBits + bytes;
            FILE* file = _wfopen(g_capture, L"wb");
            if (file) {
                ok = fwrite(&header, 1, sizeof(header), file) == sizeof(header) &&
                     fwrite(&info.bmiHeader, 1, sizeof(BITMAPINFOHEADER), file) == sizeof(BITMAPINFOHEADER) &&
                     fwrite(pixels, 1, bytes, file) == bytes;
                if (fclose(file) != 0) ok = false;
            }
        }
        SelectObject(memory, old);
        DeleteObject(bitmap);
    }
    if (memory) DeleteDC(memory);
    ReleaseDC(window, screen);
    return ok;
}

static LRESULT CALLBACK WindowProc(HWND window, UINT message, WPARAM wp, LPARAM lp) {
    if (message == WM_PAINT) {
        PAINTSTRUCT paint = {};
        HDC dc = BeginPaint(window, &paint);
        RECT title = {24, 20, 600, 64};
        SetBkMode(dc, TRANSPARENT);
        SetTextColor(dc, RGB(32, 40, 55));
        DrawTextW(dc, L"Windhawk visual playground", -1, &title, DT_LEFT | DT_SINGLELINE);
        RECT note = {24, 64, 600, 96};
        DrawTextW(dc, L"Win32 specimen: buttons, edits, lists and GDI text", -1,
                  &note, DT_LEFT | DT_SINGLELINE);
        EndPaint(window, &paint);
        ++g_paints;
        return 0;
    }
    if (message == WM_TIMER && wp == 1 && GetTickCount64() - g_started >= (ULONGLONG)g_settle) {
        KillTimer(window, 1);
        if (g_paints && Capture(window)) {
            Mark("capture written");
            g_result = 0;
        } else Mark("capture failed (or no paint observed)");
        DestroyWindow(window);
        return 0;
    }
    if (message == WM_DESTROY) {
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(window, message, wp, lp);
}

int wmain(int argc, wchar_t** argv) {
    setvbuf(stdout, nullptr, _IONBF, 0);
    const wchar_t* modPath = nullptr;
    for (int i = 1; i < argc; ++i) {
        if (!wcscmp(argv[i], L"--capture") && i + 1 < argc) g_capture = argv[++i];
        else if (!wcscmp(argv[i], L"--mod") && i + 1 < argc) modPath = argv[++i];
        else if (!wcscmp(argv[i], L"--settle-ms") && i + 1 < argc) g_settle = _wtoi(argv[++i]);
        else { Mark("invalid argument"); return 2; }
    }
    if (!g_capture || g_settle < 100 || g_settle > 10000) return 2;
    // Keep the fixture's DPI/geometry constant. Mods may still change it: record, don't resize.
    SetProcessDPIAware();
    HMODULE mod = nullptr;
    if (modPath) {
        mod = LoadLibraryW(modPath);
        if (!mod) { printf("[awt-host] load failed err=%lu\n", GetLastError()); return 3; }
        auto init = (BOOL(*)())GetProcAddress(mod, "Wh_ModInit");
        BOOL ok = init ? init() : FALSE;
        printf("[awt-host] init returned %d\n", (int)ok);
        if (!ok) return 4;
        auto apply = (BOOL(*)())GetProcAddress(mod, "AwtModInitReturned");
        if (!apply || !apply()) { Mark("hook application failed"); return 5; }
        auto after = (void(*)())GetProcAddress(mod, "Wh_ModAfterInit");
        if (after) after();
    }
    INITCOMMONCONTROLSEX controls = {sizeof(controls), ICC_STANDARD_CLASSES};
    InitCommonControlsEx(&controls);
    HINSTANCE instance = GetModuleHandleW(nullptr);
    WNDCLASSEXW wc = {};
    wc.cbSize = sizeof(wc);
    wc.lpfnWndProc = WindowProc;
    wc.hInstance = instance;
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    wc.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    wc.lpszClassName = L"AwtVisualPlayground";
    if (!RegisterClassExW(&wc)) return 6;
    RECT bounds = {0, 0, 640, 380};
    AdjustWindowRect(&bounds, WS_OVERLAPPEDWINDOW, FALSE);
    HWND window = CreateWindowExW(0, wc.lpszClassName, L"AWT playground", WS_OVERLAPPEDWINDOW,
                                  60, 60, bounds.right - bounds.left, bounds.bottom - bounds.top,
                                  nullptr, nullptr, instance, nullptr);
    if (!window) return 7;
    struct Control { const wchar_t* cls; const wchar_t* text; DWORD style; int x, y, w, h; };
    Control specimens[] = {
        {L"BUTTON", L"Primary action", BS_PUSHBUTTON, 24, 114, 180, 36},
        {L"BUTTON", L"Enable option", BS_AUTOCHECKBOX, 230, 114, 200, 36},
        {L"EDIT", L"Editable sample text", WS_BORDER | ES_AUTOHSCROLL, 24, 174, 390, 32},
        {L"LISTBOX", L"", WS_BORDER | LBS_NOTIFY, 24, 228, 390, 110},
        {L"STATIC", L"Secondary label", 0, 440, 180, 180, 32},
    };
    for (const auto& specimen : specimens) {
        HWND child = CreateWindowExW(0, specimen.cls, specimen.text,
                                      WS_CHILD | WS_VISIBLE | specimen.style,
                                      specimen.x, specimen.y, specimen.w, specimen.h,
                                      window, nullptr, instance, nullptr);
        if (!child) { DestroyWindow(window); return 8; }
        SendMessageW(child, WM_SETFONT, (WPARAM)GetStockObject(DEFAULT_GUI_FONT), FALSE);
        if (!wcscmp(specimen.cls, L"LISTBOX")) {
            SendMessageW(child, LB_ADDSTRING, 0, (LPARAM)L"First sample item");
            SendMessageW(child, LB_ADDSTRING, 0, (LPARAM)L"Second sample item");
            SendMessageW(child, LB_SETCURSEL, 0, 0);
        }
    }
    ShowWindow(window, SW_SHOWNOACTIVATE);
    RedrawWindow(window, nullptr, nullptr, RDW_INVALIDATE | RDW_UPDATENOW | RDW_ALLCHILDREN);
    g_started = GetTickCount64();
    if (!SetTimer(window, 1, 50, nullptr)) { DestroyWindow(window); return 9; }
    MSG msg;
    int received;
    while ((received = GetMessageW(&msg, nullptr, 0, 0)) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    // Do not call lifecycle twice: the current shim owns detach cleanup.
    // This is process restart, NOT DLL hot reload or real Windhawk injection.
    return received < 0 ? 10 : g_result;
}
