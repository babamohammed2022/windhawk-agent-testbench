// Probe helper: prints every top-level window class on the current desktop.
#include <windows.h>
#include <cstdio>
static BOOL CALLBACK Enum(HWND h, LPARAM) {
    wchar_t cls[256] = {};
    GetClassNameW(h, cls, 256);
    wchar_t title[256] = {};
    GetWindowTextW(h, title, 256);
    printf("[enumwins] hwnd=%p class=%S title=%S vis=%d\n", (void*)h, cls, title, (int)IsWindowVisible(h));
    return TRUE;
}
int main() {
    setvbuf(stdout, NULL, _IONBF, 0);
    printf("[enumwins] begin\n");
    EnumWindows(Enum, 0);
    printf("[enumwins] desktop children:\n");
    EnumChildWindows(GetDesktopWindow(), Enum, 0);
    printf("[enumwins] end err=%lu\n", GetLastError());
    return 0;
}
