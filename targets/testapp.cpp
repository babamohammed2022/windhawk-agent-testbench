// Small Win32 program used as a hook target: window, file I/O, registry reads.
#include <windows.h>
#include <cstdio>
#include <shellapi.h>

static LRESULT CALLBACK WndProc(HWND h, UINT m, WPARAM w, LPARAM l) {
    return DefWindowProcW(h, m, w, l);
}

int WINAPI WinMain(HINSTANCE hi, HINSTANCE, LPSTR, int) {
    setvbuf(stdout, NULL, _IONBF, 0);
    WNDCLASSEXW wc = {};
    wc.cbSize = sizeof(wc);
    wc.lpfnWndProc = WndProc;
    wc.hInstance = hi;
    wc.lpszClassName = L"AwtTestAppWnd";
    RegisterClassExW(&wc);
    HWND wnd = CreateWindowExW(0, L"AwtTestAppWnd", L"awt testapp", WS_OVERLAPPEDWINDOW,
                               100, 100, 400, 300, NULL, NULL, hi, NULL);
    printf("[testapp] window=%p class=AwtTestAppWnd\n", (void*)wnd);
    ShowWindow(wnd, SW_SHOW);
    UpdateWindow(wnd);

    int seconds = 8;
    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (argv) {
        for (int i = 1; i < argc; ++i)
            if (!wcscmp(argv[i], L"--seconds") && i + 1 < argc) seconds = _wtoi(argv[i + 1]);
        LocalFree(argv);
    }

    ULONGLONG t0 = GetTickCount64();
    int round = 0;
    MSG msg;
    while (GetTickCount64() - t0 < (ULONGLONG)seconds * 1000) {
        if (++round % 10 == 1) {
            HANDLE hf = CreateFileW(L"C:\\awt-test.txt", GENERIC_WRITE, 0, NULL,
                                    CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
            if (hf != INVALID_HANDLE_VALUE) {
                DWORD w; WriteFile(hf, "x", 1, &w, NULL); CloseHandle(hf);
                printf("[testapp] CreateFileW round %d\n", round);
            }
            HKEY key;
            if (RegOpenKeyExW(HKEY_CURRENT_USER, L"Software", 0, KEY_READ, &key) == ERROR_SUCCESS)
                RegCloseKey(key);
        }
        if (MsgWaitForMultipleObjects(0, NULL, FALSE, 100, QS_ALLINPUT) == WAIT_OBJECT_0) {
            while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
        }
    }
    printf("[testapp] exiting\n");
    return 0;
}
