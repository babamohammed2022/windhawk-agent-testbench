// automatic-windhawk-testing — loader/host v1 (M2-M4).
//
// Modes:
//   --mode self     : loads the mod DLL into THIS process (simulated host). The host
//                     then behaves like a generic Win32 program (window, file I/O,
//                     registry reads) so public-API hooks have something to fire on.
//   --mode inject   : (M4) starts --target exe, injects the mod DLL with
//                     CreateRemoteThread+LoadLibraryW, calls Wh_ModInit remotely.
//
// The host prints [awt-host] markers on stdout; the runner parses them.
#include <windows.h>
#include <shellapi.h>
#include <cstdio>
#include <cstring>
#include <cstdarg>
#include <string>

static void Mark(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    printf("[awt-host] ");
    vprintf(fmt, ap);
    printf("\n");
    fflush(stdout);
    va_end(ap);
}

typedef BOOL (*ModInitFn)();
typedef void (*ModVoidFn)();
typedef BOOL (*ModReloadFn)(BOOL*);
typedef BOOL (*AwtBoolFn)();
typedef UINT (*AwtUintFn)();

static HMODULE g_mod = NULL;

static void CallLifecycle(HMODULE m, const char* plain, const char* meaning) {
    FARPROC f = GetProcAddress(m, plain);
    if (!f) { Mark("lifecycle %s: not exported (optional)", plain); return; }
    ((ModVoidFn)f)();
    Mark("lifecycle %s: called (%s)", plain, meaning);
}

static int RunSelfMode(const wchar_t* modPath, int seconds) {
    g_mod = LoadLibraryW(modPath);
    if (!g_mod) { Mark("load failed err=%lu", GetLastError()); return 2; }
    Mark("load ok");

    auto init = (ModInitFn)GetProcAddress(g_mod, "Wh_ModInit");
    if (!init) { Mark("Wh_ModInit not exported"); return 3; }
    BOOL ok = init();
    Mark("init returned %d", (int)ok);
    if (!ok) { Mark("init aborted loading"); return 4; }

    auto applied = (AwtBoolFn)GetProcAddress(g_mod, "AwtModInitReturned");
    if (applied) { Mark("hooks applied %d", (int)applied()); }
    auto count = (AwtUintFn)GetProcAddress(g_mod, "AwtAppliedHookCount");
    if (count) Mark("hooks applied count %u", count());

    // Generic Win32 activity so public-API hooks fire inside this process.
    wchar_t tmp[MAX_PATH];
    GetTempPathW(MAX_PATH, tmp);
    wcsncat(tmp, L"awt-self.txt", MAX_PATH - wcslen(tmp) - 1);
    for (int i = 0; i < 6; ++i) {
        HANDLE hf = CreateFileW(tmp, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        if (hf != INVALID_HANDLE_VALUE) {
            DWORD w; const char* data = "awt self activity\n";
            WriteFile(hf, data, (DWORD)strlen(data), &w, NULL);
            CloseHandle(hf);
        }
        Sleep(120);
    }
    DeleteFileW(tmp);

    // tray client activity (NIM_ADD / NIM_MODIFY / NIM_DELETE)
    NOTIFYICONDATAW nid = {};
    nid.cbSize = sizeof(nid);
    nid.hWnd = CreateWindowExW(0, L"STATIC", L"awt-tray", 0, 0, 0, 0, 0, NULL, NULL, NULL, NULL);
    nid.uID = 1;
    nid.uFlags = NIF_MESSAGE | NIF_TIP;
    nid.uCallbackMessage = WM_USER + 1;
    wcsncpy(nid.szTip, L"awt tray", 63);
    Shell_NotifyIconW(NIM_ADD, &nid);
    Sleep(200);
    wcsncpy(nid.szTip, L"awt tray modified", 63);
    Shell_NotifyIconW(NIM_MODIFY, &nid);
    Sleep(200);
    Shell_NotifyIconW(NIM_DELETE, &nid);

    // pump messages for the requested time so window hooks/subclasses live
    ULONGLONG t0 = GetTickCount64();
    MSG msg;
    while (GetTickCount64() - t0 < (ULONGLONG)seconds * 1000) {
        if (MsgWaitForMultipleObjects(0, NULL, FALSE, 100, QS_ALLINPUT) == WAIT_OBJECT_0) {
            while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
        }
    }

    auto reload = (ModReloadFn)GetProcAddress(g_mod, "Wh_ModSettingsChangedReload");
    if (reload) { BOOL r = FALSE; reload(&r); Mark("settings changed (reload=%d)", (int)r); }
    CallLifecycle(g_mod, "Wh_ModBeforeUninit", "before uninit");
    CallLifecycle(g_mod, "Wh_ModUninit", "uninit");
    FreeLibrary(g_mod);
    Mark("self mode done");
    return 0;
}

// ---------------------------------------------------------------- inject mode (M4)
// Injection method: Windows' AppInit_DLLs mechanism (documented in the mega-prompt,
// step F). Remote CreateRemoteThread+LoadLibraryW was tried first and abandoned:
// under Wine the image mapped by a remote LoadLibraryW kept a non-executable first
// .text page (execute page fault on the mod entry point), see docs/limitations.md.
static void SetAppInit(const wchar_t* modPath, bool on) {
    HKEY key;
    if (RegCreateKeyExW(HKEY_LOCAL_MACHINE,
                        L"SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Windows",
                        0, NULL, 0, KEY_SET_VALUE, NULL, &key, NULL) != ERROR_SUCCESS)
        return;
    if (on) {
        RegSetValueExW(key, L"AppInit_DLLs", 0, REG_SZ, (const BYTE*)modPath,
                       (DWORD)((wcslen(modPath) + 1) * sizeof(wchar_t)));
        DWORD one = 1;
        RegSetValueExW(key, L"LoadAppInit_DLLs", 0, REG_DWORD, (const BYTE*)&one, sizeof(one));
    } else {
        RegSetValueExW(key, L"AppInit_DLLs", 0, REG_SZ, (const BYTE*)L"", sizeof(wchar_t));
        DWORD zero = 0;
        RegSetValueExW(key, L"LoadAppInit_DLLs", 0, REG_DWORD, (const BYTE*)&zero, sizeof(zero));
    }
    RegCloseKey(key);
}

static int RunInjectMode(const wchar_t* modPath, const wchar_t* targetPath, int seconds) {
    SetAppInit(modPath, true);
    Mark("AppInit_DLLs set to %S", modPath);
    STARTUPINFOW si = {};
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi = {};
    if (!CreateProcessW(targetPath, NULL, NULL, NULL, FALSE, 0, NULL, NULL, &si, &pi)) {
        Mark("target spawn failed err=%lu", GetLastError());
        SetAppInit(modPath, false);
        return 5;
    }
    Mark("target spawned pid=%lu", pi.dwProcessId);
    WaitForSingleObject(pi.hProcess, (DWORD)seconds * 1000);
    bool timedOut = WaitForSingleObject(pi.hProcess, 0) == WAIT_TIMEOUT;
    if (timedOut) { Mark("target still running after %d s, terminating", seconds); TerminateProcess(pi.hProcess, 9); }
    SetAppInit(modPath, false);
    DWORD exitCode = 0;
    GetExitCodeProcess(pi.hProcess, &exitCode);
    Mark("target exit code %lu", exitCode);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    Mark("inject mode done");
    return timedOut ? 8 : 0;
}

int wmain(int argc, wchar_t** argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    const wchar_t* modPath = NULL;
    const wchar_t* targetPath = NULL;
    const wchar_t* mode = L"self";
    int seconds = 5;
    for (int i = 1; i < argc; ++i) {
        if (!wcscmp(argv[i], L"--mod") && i + 1 < argc) modPath = argv[++i];
        else if (!wcscmp(argv[i], L"--target") && i + 1 < argc) targetPath = argv[++i];
        else if (!wcscmp(argv[i], L"--mode") && i + 1 < argc) mode = argv[++i];
        else if (!wcscmp(argv[i], L"--seconds") && i + 1 < argc) seconds = _wtoi(argv[++i]);
    }
    if (!modPath) { Mark("usage: awt-host --mod x.dll [--mode self|inject] [--target t.exe] [--seconds N]"); return 1; }
    Mark("start mode=%S mod=%S seconds=%d", mode, modPath, seconds);
    if (!wcscmp(mode, L"inject") && targetPath) return RunInjectMode(modPath, targetPath, seconds);
    return RunSelfMode(modPath, seconds);
}
