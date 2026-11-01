// Real tray client: adds/modifies/removes a notification icon and reacts to TaskbarCreated.
#include <windows.h>
#include <shellapi.h>
#include <cstdio>

static UINT g_taskbarCreated = 0;
static LRESULT CALLBACK WndProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    if (m == WM_USER + 1) { printf("[trayapp] callback lParam=%lu\n", (unsigned long)l); fflush(stdout); }
    if (g_taskbarCreated && m == g_taskbarCreated) { printf("[trayapp] TaskbarCreated\n"); fflush(stdout); }
    return DefWindowProcW(h, m, w, l);
}

int WINAPI WinMain(HINSTANCE hi, HINSTANCE, LPSTR, int) {
    setvbuf(stdout, NULL, _IONBF, 0);
    g_taskbarCreated = RegisterWindowMessageW(L"TaskbarCreated");
    WNDCLASSEXW wc = {};
    wc.cbSize = sizeof(wc); wc.lpfnWndProc = WndProc; wc.hInstance = hi; wc.lpszClassName = L"AwtTrayWnd";
    RegisterClassExW(&wc);
    HWND wnd = CreateWindowExW(0, L"AwtTrayWnd", L"awt trayapp", 0, 0, 0, 0, 0, NULL, NULL, hi, NULL);
    NOTIFYICONDATAW nid = {};
    nid.cbSize = sizeof(nid); nid.hWnd = wnd; nid.uID = 7;
    nid.uFlags = NIF_MESSAGE | NIF_TIP; nid.uCallbackMessage = WM_USER + 1;
    wcsncpy(nid.szTip, L"awt trayapp icon", 63);
    printf("[trayapp] NIM_ADD=%d\n", (int)Shell_NotifyIconW(NIM_ADD, &nid)); fflush(stdout);
    ULONGLONG t0 = GetTickCount64();
    MSG msg;
    while (GetTickCount64() - t0 < 8000) {
        if (MsgWaitForMultipleObjects(0, NULL, FALSE, 100, QS_ALLINPUT) == WAIT_OBJECT_0)
            while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
    }
    printf("[trayapp] NIM_DELETE=%d\n", (int)Shell_NotifyIconW(NIM_DELETE, &nid)); fflush(stdout);
    return 0;
}
