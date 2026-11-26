// A simulated desktop for repeatable Windhawk mod experiments.
//
// This is an original Win32 program written for this bench. It is NOT Explorer,
// NOT a Windows shell replacement and NOT built from ReactOS or leaked sources.
// It draws its own surface, taskbar and notification area, exposes a small
// scripted action language, and saves a top-down 32-bit BMP of its client area.
// All observations it prints are about THIS process and THIS simulated scene.
#include <windows.h>
#include <shellapi.h>
#include <cstdio>
#include <cwchar>
#include <sstream>
#include <string>
#include <vector>

struct DeskWindow {
    std::wstring id;
    std::wstring title;
    std::wstring heading;
    std::wstring body;
    HWND hwnd = nullptr;
    int x = 0;
    int y = 0;
    int w = 0;
    int h = 0;
    bool minimized = false;
    bool closed = false;
};

static HWND g_surface = nullptr;
static HWND g_taskbar_strip = nullptr;
static std::vector<DeskWindow> g_windows;
static std::wstring g_active;
static std::wstring g_profile = L"win10";
static bool g_shell_conflict = false;
static bool g_appbar_registered = false;
static bool g_notice = false;
static const wchar_t* g_capture = nullptr;
static int g_settle = 500;
static ULONGLONG g_started = 0;
static int g_result = 1;
static unsigned g_paints = 0;

static const COLORREF kDesktop = RGB(31, 44, 64);
static const COLORREF kTaskbar = RGB(22, 30, 44);
static const COLORREF kWindow = RGB(244, 247, 251);
static const COLORREF kTitleActive = RGB(48, 104, 168);
static const COLORREF kTitleIdle = RGB(96, 108, 126);
static const COLORREF kButton = RGB(226, 232, 240);
static const COLORREF kNotice = RGB(250, 220, 120);
static const COLORREF kTray = RGB(118, 206, 176);

static void Mark(const char* text) {
    printf("[awt-desktop] %s\n", text);
    fflush(stdout);
}

static void MarkW(const char* label, const std::wstring& value) {
    printf("[awt-desktop] %s=%ls\n", label, value.c_str());
    fflush(stdout);
}

static void LogError(const char* what, DWORD error) {
    printf("[awt-desktop] error %s err=%lu\n", what, (unsigned long)error);
    fflush(stdout);
}

static LONG WINAPI DesktopExceptionFilter(EXCEPTION_POINTERS* info) {
    DWORD code = info && info->ExceptionRecord ? info->ExceptionRecord->ExceptionCode : 0;
    void* address = info && info->ExceptionRecord ? info->ExceptionRecord->ExceptionAddress : nullptr;
    printf("[awt-desktop] unhandled-exception code=0x%08lx address=%p\n",
           (unsigned long)code, address);
    fflush(stdout);
    return EXCEPTION_EXECUTE_HANDLER;
}

static BOOL CALLBACK ProbeWindow(HWND window, LPARAM) {
    if (!IsWindowVisible(window)) return TRUE;
    wchar_t cls[128] = {};
    wchar_t title[128] = {};
    GetClassNameW(window, cls, 128);
    GetWindowTextW(window, title, 128);
    printf("[awt-desktop] probe class=%ls title=%ls\n", cls, title);
    fflush(stdout);
    if (!wcscmp(cls, L"Shell_TrayWnd")) g_shell_conflict = true;
    return TRUE;
}

static void ProbeDesktop() {
    Mark("probe begin");
    EnumWindows(ProbeWindow, 0);
    printf("[awt-desktop] probe end shell_tray_conflict=%d\n", g_shell_conflict ? 1 : 0);
    fflush(stdout);
}

static void RegisterAppBar() {
    APPBARDATA bar = {};
    bar.cbSize = sizeof(bar);
    bar.hWnd = g_surface;
    bar.uEdge = ABE_BOTTOM;
    int screen_x = GetSystemMetrics(SM_CXSCREEN);
    int screen_y = GetSystemMetrics(SM_CYSCREEN);
    bar.rc.left = 0;
    bar.rc.top = screen_y - 52;
    bar.rc.right = screen_x;
    bar.rc.bottom = screen_y;
    UINT_PTR added = SHAppBarMessage(ABM_NEW, &bar);
    printf("[awt-desktop] appbar ABM_NEW returned=%llu\n",
           (unsigned long long)added);
    fflush(stdout);
    if (added) {
        UINT_PTR positioned = SHAppBarMessage(ABM_SETPOS, &bar);
        printf("[awt-desktop] appbar ABM_SETPOS returned=%llu bottom=%ld\n",
               (unsigned long long)positioned, (long)bar.rc.bottom);
        fflush(stdout);
        g_appbar_registered = true;
    } else {
        LogError("appbar ABM_NEW unavailable", GetLastError());
    }
    RECT work = {};
    if (SystemParametersInfoW(SPI_GETWORKAREA, 0, &work, 0)) {
        printf("[awt-desktop] workarea left=%ld top=%ld right=%ld bottom=%ld\n",
               (long)work.left, (long)work.top, (long)work.right, (long)work.bottom);
        fflush(stdout);
    } else {
        LogError("SPI_GETWORKAREA unavailable", GetLastError());
    }
}

static void UnregisterAppBar() {
    if (!g_appbar_registered) return;
    APPBARDATA bar = {};
    bar.cbSize = sizeof(bar);
    bar.hWnd = g_surface;
    UINT_PTR removed = SHAppBarMessage(ABM_REMOVE, &bar);
    printf("[awt-desktop] appbar ABM_REMOVE returned=%llu\n",
           (unsigned long long)removed);
    fflush(stdout);
    g_appbar_registered = false;
}

static DeskWindow* Find(const std::wstring& id) {
    for (auto& window : g_windows) {
        if (window.id == id) return &window;
    }
    return nullptr;
}

static RECT ButtonRect(const DeskWindow& window) {
    (void)window;
    RECT rect = {16, 92, 216, 128};
    return rect;
}

static void DrawLabel(HDC dc, RECT rect, const wchar_t* text, COLORREF color) {
    COLORREF previous = SetTextColor(dc, color);
    int mode = SetBkMode(dc, TRANSPARENT);
    DrawTextW(dc, text, -1, &rect, DT_LEFT | DT_SINGLELINE | DT_VCENTER);
    SetTextColor(dc, previous);
    SetBkMode(dc, mode);
}

static void Fill(HDC dc, const RECT& rect, COLORREF color) {
    HBRUSH brush = CreateSolidBrush(color);
    if (brush) {
        FillRect(dc, &rect, brush);
        DeleteObject(brush);
    }
}

static void RedrawScene() {
    if (g_surface) InvalidateRect(g_surface, nullptr, TRUE);
    for (const auto& window : g_windows) {
        if (window.hwnd) InvalidateRect(window.hwnd, nullptr, TRUE);
    }
}

// Public GDI drawing of a Windows-10-like bar: Start, search, task buttons and
// a notification area. These are pixels in OUR window, not native shell controls.
static void DrawTaskbar(HDC dc, int width, int top, int bottom) {
    RECT bar = {0, top, width, bottom};
    Fill(dc, bar, kTaskbar);
    int y = top;
    bool win10 = g_profile == L"win10";

    if (win10) {
        RECT start = {8, y + 7, 72, y + 45};
        Fill(dc, start, kTitleActive);
        RECT start_text = {14, y + 9, 70, y + 43};
        DrawLabel(dc, start_text, L"Start", RGB(255, 255, 255));
        RECT search = {80, y + 9, 252, y + 43};
        Fill(dc, search, RGB(245, 247, 250));
        RECT search_text = {90, y + 10, 248, y + 42};
        DrawLabel(dc, search_text, L"Search", RGB(90, 98, 110));
        RECT view = {260, y + 9, 302, y + 43};
        Fill(dc, view, kTitleIdle);
        DrawLabel(dc, view, L"[]", RGB(255, 255, 255));
    } else {
        RECT label = {16, y + 8, 320, y + 44};
        DrawLabel(dc, label, L"AWT simulated taskbar", RGB(226, 235, 248));
    }

    int x = win10 ? 312 : 340;
    for (const auto& window : g_windows) {
        if (window.closed) continue;
        RECT item = {x, y + 8, x + 124, y + 44};
        Fill(dc, item, window.id == g_active ? kTitleActive : kTitleIdle);
        RECT text = {x + 8, y + 9, x + 120, y + 43};
        std::wstring caption = window.minimized ? L"o " : L"| ";
        caption += window.id;
        DrawLabel(dc, text, caption.c_str(), RGB(255, 255, 255));
        x += 132;
    }

    RECT clock = {width - 176, y + 8, width - 74, y + 44};
    DrawLabel(dc, clock, L"12:00", RGB(230, 238, 248));
    RECT tray = {width - 44, y + 8, width - 18, y + 42};
    Fill(dc, tray, kTray);
}

static void DrawNotice(HDC dc, int width, int height) {
    if (!g_notice) return;
    RECT bubble = {width - 274, height - 210, width - 20, height - 58};
    Fill(dc, bubble, kNotice);
    RECT note = {width - 262, height - 196, width - 34, height - 74};
    DrawLabel(dc, note, L"Simulated notification", RGB(40, 36, 24));
}

// Top-down, uncompressed BGRA BMP. The Python side validates and converts it.
static bool CaptureWindow(HWND window) {
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
                     fwrite(&info.bmiHeader, 1, sizeof(BITMAPINFOHEADER), file) ==
                         sizeof(BITMAPINFOHEADER) &&
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
    DeskWindow* item = nullptr;
    for (auto& candidate : g_windows) {
        if (candidate.hwnd == window) { item = &candidate; break; }
    }
    if (message == WM_PAINT && item) {
        PAINTSTRUCT paint = {};
        HDC dc = BeginPaint(window, &paint);
        RECT client = {};
        GetClientRect(window, &client);
        Fill(dc, client, kWindow);
        RECT title = {0, 0, client.right, 36};
        Fill(dc, title, item->id == g_active ? kTitleActive : kTitleIdle);
        RECT title_text = {12, 4, client.right - 8, 32};
        DrawLabel(dc, title_text, item->title.c_str(), RGB(255, 255, 255));
        RECT heading = {16, 48, client.right - 16, 84};
        DrawLabel(dc, heading, item->heading.c_str(), RGB(32, 40, 55));
        RECT body = {16, 136, client.right - 16, 176};
        DrawLabel(dc, body, item->body.c_str(), RGB(72, 84, 104));
        RECT button = ButtonRect(*item);
        Fill(dc, button, kButton);
        RECT button_text = {button.left + 8, button.top, button.right - 8, button.bottom};
        DrawLabel(dc, button_text, L"Toggle notice", RGB(28, 40, 60));
        EndPaint(window, &paint);
        return 0;
    }
    if (message == WM_LBUTTONDOWN && item) {
        int x = LOWORD(lp);
        int y = HIWORD(lp);
        RECT button = ButtonRect(*item);
        if (x >= button.left && x <= button.right && y >= button.top && y <= button.bottom) {
            g_notice = !g_notice;
            printf("[awt-desktop] action click id=%ls target=notice before=%d after=%d ok=1\n",
                   item->id.c_str(), g_notice ? 0 : 1, g_notice ? 1 : 0);
            fflush(stdout);
            RedrawScene();
        } else {
            g_active = item->id;
            printf("[awt-desktop] action click id=%ls target=client ok=1\n", item->id.c_str());
            fflush(stdout);
            RedrawScene();
        }
        return 0;
    }
    if (message == WM_KEYDOWN && item) {
        printf("[awt-desktop] action key id=%ls vk=%lu ok=1\n",
               item->id.c_str(), (unsigned long)wp);
        fflush(stdout);
        if (wp == VK_ESCAPE) g_notice = false;
        RedrawScene();
        return 0;
    }
    return DefWindowProcW(window, message, wp, lp);
}

// Optional child strip: in the Win10 profile we register a window called
// Shell_TrayWnd when no such window exists on this desktop. It is drawn by us
// from public GDI calls; the name is an approximation, not the real shell.
static LRESULT CALLBACK StripProc(HWND window, UINT message, WPARAM wp, LPARAM lp) {
    if (message == WM_PAINT) {
        PAINTSTRUCT paint = {};
        HDC dc = BeginPaint(window, &paint);
        RECT client = {};
        GetClientRect(window, &client);
        DrawTaskbar(dc, client.right, 0, client.bottom);
        EndPaint(window, &paint);
        return 0;
    }
    return DefWindowProcW(window, message, wp, lp);
}

static LRESULT CALLBACK SurfaceProc(HWND window, UINT message, WPARAM wp, LPARAM lp) {
    if (message == WM_PAINT) {
        PAINTSTRUCT paint = {};
        HDC dc = BeginPaint(window, &paint);
        RECT client = {};
        GetClientRect(window, &client);
        Fill(dc, client, kDesktop);
        for (int x = 0; x < client.right; x += 80) {
            RECT stripe = {x, 0, x + 2, client.bottom - 52};
            Fill(dc, stripe, RGB(36, 51, 73));
        }
        if (!g_taskbar_strip) {
            DrawTaskbar(dc, client.right, client.bottom - 52, client.bottom);
        }
        DrawNotice(dc, client.right, client.bottom);
        EndPaint(window, &paint);
        ++g_paints;
        return 0;
    }
    if (message == WM_TIMER && wp == 1 &&
        GetTickCount64() - g_started >= (ULONGLONG)g_settle) {
        KillTimer(window, 1);
        UpdateWindow(window);
        for (const auto& item : g_windows) {
            if (item.hwnd) UpdateWindow(item.hwnd);
        }
        if (g_paints && CaptureWindow(window)) {
            Mark("capture written");
            g_result = 0;
        } else {
            LogError("capture failed (or no paint observed)", GetLastError());
        }
        DestroyWindow(window);
        return 0;
    }
    if (message == WM_DESTROY) {
        UnregisterAppBar();
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(window, message, wp, lp);
}

static bool CreateWindow_(const std::wstring& id, const std::wstring& title,
                          const std::wstring& heading, const std::wstring& body,
                          int x, int y, int w, int h) {
    if (Find(id)) {
        printf("[awt-desktop] action create id=%ls ok=0 reason=duplicate\n", id.c_str());
        fflush(stdout);
        return false;
    }
    DeskWindow item;
    item.id = id;
    item.title = title;
    item.heading = heading;
    item.body = body;
    item.x = x;
    item.y = y;
    item.w = w;
    item.h = h;
    item.hwnd = CreateWindowExW(0, L"AwtDesktopWindow", title.c_str(),
                                WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS,
                                x, y, w, h, g_surface, nullptr,
                                GetModuleHandleW(nullptr), nullptr);
    if (!item.hwnd) {
        printf("[awt-desktop] action create id=%ls ok=0 reason=CreateWindowExW err=%lu\n",
               id.c_str(), GetLastError());
        fflush(stdout);
        return false;
    }
    g_windows.push_back(item);
    g_active = id;
    RedrawScene();
    printf("[awt-desktop] action create id=%ls class=AwtDesktopWindow title=%ls ok=1\n",
           id.c_str(), title.c_str());
    fflush(stdout);
    return true;
}

static UINT KeyCode(const std::wstring& name) {
    if (name == L"TAB") return VK_TAB;
    if (name == L"ESC") return VK_ESCAPE;
    if (name == L"SPACE") return VK_SPACE;
    if (name == L"ENTER") return VK_RETURN;
    return 0;
}

static bool InjectMouseClick(int x, int y) {
    int screen_x = GetSystemMetrics(SM_CXSCREEN);
    int screen_y = GetSystemMetrics(SM_CYSCREEN);
    if (screen_x <= 0 || screen_y <= 0) return false;
    INPUT input = {};
    input.type = INPUT_MOUSE;
    input.mi.dx = (LONG)(x * 65535 / screen_x);
    input.mi.dy = (LONG)(y * 65535 / screen_y);
    input.mi.dwFlags = MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE;
    if (SendInput(1, &input, sizeof(input)) != 1) return false;
    input = {};
    input.type = INPUT_MOUSE;
    input.mi.dwFlags = MOUSEEVENTF_LEFTDOWN;
    if (SendInput(1, &input, sizeof(input)) != 1) return false;
    input.mi.dwFlags = MOUSEEVENTF_LEFTUP;
    return SendInput(1, &input, sizeof(input)) == 1;
}

static bool InjectKey(UINT vk) {
    if (!vk) return false;
    INPUT input = {};
    input.type = INPUT_KEYBOARD;
    input.ki.wVk = (WORD)vk;
    if (SendInput(1, &input, sizeof(input)) != 1) return false;
    input.ki.dwFlags = KEYEVENTF_KEYUP;
    return SendInput(1, &input, sizeof(input)) == 1;
}

static bool ApplyAction(const std::vector<std::wstring>& tokens) {
    const std::wstring& action = tokens[0];
    if (action == L"create" && tokens.size() >= 3) {
        std::wstring id = tokens[1];
        std::wstring title = tokens[2];
        std::wstring heading = tokens.size() > 3 ? tokens[3] : title;
        std::wstring body = tokens.size() > 4 ? tokens[4] : L"Simulated test window";
        bool created = CreateWindow_(id, title, heading, body,
                                     60 + (int)g_windows.size() % 2 * 440,
                                     70 + (int)g_windows.size() % 2 * 40, 410, 230);
        return created;
    }
    if ((action == L"activate" || action == L"minimize" || action == L"restore" ||
         action == L"close" || action == L"click" || action == L"key") &&
        tokens.size() >= 2) {
        DeskWindow* item = Find(tokens[1]);
        if (!item || item->closed) {
            printf("[awt-desktop] action %ls id=%ls ok=0 reason=not-found\n",
                   action.c_str(), tokens[1].c_str());
            fflush(stdout);
            return false;
        }
        if (action == L"activate") {
            g_active = item->id;
            if (item->minimized) {
                ShowWindow(item->hwnd, SW_SHOW);
                item->minimized = false;
            }
            SetWindowPos(item->hwnd, HWND_TOP, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW);
        } else if (action == L"minimize") {
            ShowWindow(item->hwnd, SW_HIDE);
            item->minimized = true;
            if (g_active == item->id) g_active.clear();
        } else if (action == L"restore") {
            ShowWindow(item->hwnd, SW_SHOW);
            item->minimized = false;
            g_active = item->id;
        } else if (action == L"close") {
            DestroyWindow(item->hwnd);
            item->hwnd = nullptr;
            item->closed = true;
            if (g_active == item->id) g_active.clear();
        } else if (action == L"click") {
            RECT button = ButtonRect(*item);
            int x = (button.left + button.right) / 2;
            int y = (button.top + button.bottom) / 2;
            SendMessageW(item->hwnd, WM_LBUTTONDOWN, MK_LBUTTON, MAKELPARAM(x, y));
            SendMessageW(item->hwnd, WM_LBUTTONUP, 0, MAKELPARAM(x, y));
        } else if (action == L"key") {
            UINT vk = tokens.size() > 2 ? KeyCode(tokens[2]) : VK_TAB;
            if (!vk) {
                printf("[awt-desktop] action key id=%ls ok=0 reason=unknown-key\n",
                       item->id.c_str());
                fflush(stdout);
                return false;
            }
            SendMessageW(item->hwnd, WM_KEYDOWN, vk, 0);
            SendMessageW(item->hwnd, WM_KEYUP, vk, 0);
        }
        RedrawScene();
        // Canonical markers consumed by the scenario assertions: keep the
        // minimize/close spelling stable, with the state field they check.
        if (action == L"minimize") {
            printf("[awt-desktop] action minimize id=%ls minimized=%d ok=1\n",
                   item->id.c_str(), item->minimized ? 1 : 0);
        } else if (action == L"close") {
            printf("[awt-desktop] action close id=%ls closed=%d ok=1\n",
                   item->id.c_str(), item->closed ? 1 : 0);
        } else {
            printf("[awt-desktop] action %ls id=%ls minimized=%d closed=%d ok=1\n",
                   action.c_str(), item->id.c_str(), item->minimized ? 1 : 0,
                   item->closed ? 1 : 0);
        }
        fflush(stdout);
        return true;
    }
    if (action == L"notify" && tokens.size() >= 2) {
        g_notice = tokens[1] == L"on";
        RedrawScene();
        printf("[awt-desktop] action notify state=%d ok=1\n", g_notice ? 1 : 0);
        fflush(stdout);
        return true;
    }
    if (action == L"input-click" && tokens.size() >= 3) {
        bool ok = InjectMouseClick(_wtoi(tokens[1].c_str()), _wtoi(tokens[2].c_str()));
        printf("[awt-desktop] action input-click reported=%d ok=%d\n", ok ? 1 : 0, ok ? 1 : 0);
        fflush(stdout);
        return ok;
    }
    if (action == L"input-key" && tokens.size() >= 2) {
        bool ok = InjectKey(KeyCode(tokens[1]));
        printf("[awt-desktop] action input-key reported=%d ok=%d\n", ok ? 1 : 0, ok ? 1 : 0);
        fflush(stdout);
        return ok;
    }
    if (action == L"wait" && tokens.size() >= 2) {
        DWORD ms = (DWORD)_wtoi(tokens[1].c_str());
        if (ms > 5000) ms = 5000;
        Sleep(ms);
        printf("[awt-desktop] action wait ms=%lu ok=1\n", (unsigned long)ms);
        fflush(stdout);
        return true;
    }
    if (action == L"capture") {
        Mark("action capture scheduled ok=1");
        return true;
    }
    printf("[awt-desktop] action %ls ok=0 reason=unknown-command\n", action.c_str());
    fflush(stdout);
    return false;
}

// The action language uses single-quoted arguments (e.g. a window title with
// spaces). Split on whitespace outside quotes so the exact heading reaches
// DrawTextW; a plain >> token stream would truncate it.
static std::vector<std::wstring> TokenizeAction(const std::wstring& text) {
    std::vector<std::wstring> tokens;
    std::wstring current;
    wchar_t quote = 0;
    bool started = false;
    auto space = [](wchar_t c) {
        return c == L' ' || c == L'\t' || c == L'\r' || c == L'\n';
    };
    for (wchar_t c : text) {
        if (quote) {
            if (c == quote) quote = 0;
            else current += c;
        } else if (c == L'\'' || c == L'"') {
            quote = c;
            started = true;
        } else if (space(c)) {
            if (started) { tokens.push_back(current); current.clear(); started = false; }
        } else {
            current += c;
            started = true;
        }
    }
    if (started) tokens.push_back(current);
    return tokens;
}

static bool RunScript(const wchar_t* path) {
    FILE* file = _wfopen(path, L"r");
    if (!file) {
        printf("[awt-desktop] script open failed err=%lu\n", GetLastError());
        fflush(stdout);
        return false;
    }
    wchar_t line[1024] = {};
    bool ok = true;
    while (fgetws(line, 1024, file)) {
        std::wstring text(line);
        size_t comment = text.find(L'#');
        if (comment != std::wstring::npos) text.erase(comment);
        std::vector<std::wstring> tokens = TokenizeAction(text);
        if (tokens.empty()) continue;
        if (!ApplyAction(tokens)) ok = false;
    }
    fclose(file);
    return ok;
}

int wmain(int argc, wchar_t** argv) {
    setvbuf(stdout, nullptr, _IONBF, 0);
    const wchar_t* mod_path = nullptr;
    const wchar_t* script_path = nullptr;
    for (int i = 1; i < argc; ++i) {
        if (!wcscmp(argv[i], L"--capture") && i + 1 < argc) g_capture = argv[++i];
        else if (!wcscmp(argv[i], L"--mod") && i + 1 < argc) mod_path = argv[++i];
        else if (!wcscmp(argv[i], L"--actions") && i + 1 < argc) script_path = argv[++i];
        else if (!wcscmp(argv[i], L"--settle-ms") && i + 1 < argc) g_settle = _wtoi(argv[++i]);
        else if (!wcscmp(argv[i], L"--profile") && i + 1 < argc) g_profile = argv[++i];
        else { Mark("invalid argument"); return 2; }
    }
    if (!g_capture || !script_path || g_settle < 100 || g_settle > 10000) return 2;
    if (g_profile != L"win10" && g_profile != L"basic") return 2;
    SetProcessDPIAware();
    SetUnhandledExceptionFilter(DesktopExceptionFilter);
    MarkW("profile", g_profile);

    if (mod_path) {
        HMODULE mod = LoadLibraryW(mod_path);
        if (!mod) {
            printf("[awt-host] load failed err=%lu\n", GetLastError());
            fflush(stdout);
            return 3;
        }
        auto init = (BOOL(*)())GetProcAddress(mod, "Wh_ModInit");
        BOOL ok = init ? init() : FALSE;
        printf("[awt-host] init returned %d\n", (int)ok);
        fflush(stdout);
        if (!ok) {
            LogError("mod init returned false", 4);
            return 4;
        }
        auto applied = (BOOL(*)())GetProcAddress(mod, "AwtModInitReturned");
        if (!applied || !applied()) { Mark("hook application failed"); return 5; }
        auto after = (void(*)())GetProcAddress(mod, "Wh_ModAfterInit");
        if (after) after();
    }

    ProbeDesktop();

    HINSTANCE instance = GetModuleHandleW(nullptr);
    WNDCLASSEXW surface_class = {};
    surface_class.cbSize = sizeof(surface_class);
    surface_class.lpfnWndProc = SurfaceProc;
    surface_class.hInstance = instance;
    surface_class.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    surface_class.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    surface_class.lpszClassName = L"AwtDesktopSurface";
    if (!RegisterClassExW(&surface_class)) {
        LogError("RegisterClassExW surface", GetLastError());
        return 6;
    }

    WNDCLASSEXW window_class = {};
    window_class.cbSize = sizeof(window_class);
    window_class.lpfnWndProc = WindowProc;
    window_class.hInstance = instance;
    window_class.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    window_class.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    window_class.lpszClassName = L"AwtDesktopWindow";
    if (!RegisterClassExW(&window_class)) {
        LogError("RegisterClassExW window", GetLastError());
        return 7;
    }

    bool emulate_shell = g_profile == L"win10" && !g_shell_conflict;
    const wchar_t* strip_class = emulate_shell ? L"Shell_TrayWnd" : L"AwtTaskbarStrip";
    WNDCLASSEXW strip_class_def = {};
    strip_class_def.cbSize = sizeof(strip_class_def);
    strip_class_def.lpfnWndProc = StripProc;
    strip_class_def.hInstance = instance;
    strip_class_def.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    strip_class_def.lpszClassName = strip_class;
    if (!RegisterClassExW(&strip_class_def)) {
        LogError("RegisterClassExW strip", GetLastError());
        emulate_shell = false;
        strip_class = L"AwtTaskbarStrip";
        strip_class_def.lpszClassName = strip_class;
        if (!RegisterClassExW(&strip_class_def)) {
            LogError("RegisterClassExW fallback strip", GetLastError());
            return 7;
        }
    }
    printf("[awt-desktop] shell_class_emulation=%d class=%ls\n",
           emulate_shell ? 1 : 0, strip_class);
    fflush(stdout);

    // Force a 960x600 client area; window decorations differ between builds.
    RECT bounds = {0, 0, 960, 600};
    AdjustWindowRect(&bounds, WS_OVERLAPPEDWINDOW, FALSE);
    g_surface = CreateWindowExW(0, L"AwtDesktopSurface", L"AWT simulated desktop",
                                WS_OVERLAPPEDWINDOW, 0, 0,
                                bounds.right - bounds.left, bounds.bottom - bounds.top,
                                nullptr, nullptr, instance, nullptr);
    if (!g_surface) {
        LogError("CreateWindowExW surface", GetLastError());
        return 8;
    }
    g_taskbar_strip = CreateWindowExW(0, strip_class, L"", WS_CHILD | WS_VISIBLE,
                                      0, 600 - 52, 960, 52, g_surface, nullptr,
                                      instance, nullptr);
    if (!g_taskbar_strip) {
        LogError("CreateWindowExW strip", GetLastError());
        g_taskbar_strip = nullptr;
    }
    RegisterAppBar();
    ShowWindow(g_surface, SW_SHOWNOACTIVATE);
    UpdateWindow(g_surface);

    // A second, real EnumWindows pass over the created scene. The first probe
    // runs before any window exists, so on a bare Xvfb display it can honestly
    // enumerate nothing; record the visible windows of THIS process too.
    Mark("probe scene begin");
    EnumWindows(ProbeWindow, 0);
    Mark("probe scene end");

    bool script_ok = RunScript(script_path);
    if (!script_ok) {
        Mark("script contained a failed action");
        LogError("scripted action failed", 0);
    }

    RedrawScene();
    UpdateWindow(g_surface);
    for (const auto& item : g_windows) {
        if (item.hwnd) UpdateWindow(item.hwnd);
    }
    g_started = GetTickCount64();
    if (!SetTimer(g_surface, 1, 50, nullptr)) {
        LogError("SetTimer", GetLastError());
        DestroyWindow(g_surface);
        return 9;
    }

    MSG message;
    int received;
    while ((received = GetMessageW(&message, nullptr, 0, 0)) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    // The shim owns process-detach cleanup; do not call Wh_ModUninit again.
    if (received < 0) return 10;
    return (g_result == 0 && script_ok) ? 0 : 1;
}
