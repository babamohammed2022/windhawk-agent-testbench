// automatic-windhawk-testing — Windhawk API shim (M2/M3).
//
// Implements the engine side of the official mod API: mods compiled with WH_MOD
// include the official <windhawk_api.h>, whose inline wrappers call the extern-"C"
// InternalWh_* functions below. The shim is linked INTO the mod DLL, so a mod loaded
// anywhere (in-process loader or injected into a target) carries its engine with it.
//
// Status of each area (keep in sync with docs/shim-status.md):
//   fully implemented : Wh_Log, settings (int/string, printf-style names), storage
//                       (int/string/binary/delete/path), lifecycle plumbing,
//                       Wh_SetFunctionHook / Wh_RemoveFunctionHook /
//                       Wh_ApplyHookOperations (MinHook), WindhawkUtils::SetFunctionHook.
//   simulated w/ limits: Wh_FindFirstSymbol/Next/Close and Wh_HookSymbols return
//                       failure and log "[SHIM] symbol lookup not available in
//                       sandbox" (no Microsoft PDBs under Wine); counted in report.
//   stubbed (logged)  : dynamic-select options, Wh_Disasm, Wh_GetUrlContent.
#include <windows.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cwchar>
#include <string>
#include <vector>
#include <map>
#include <mutex>

#include <MinHook.h>

// Official engine-side declarations: the definitions below must match them exactly.
#include "windhawk_api_internal.h"
#include "windhawk_api.h"

// Defined (inline) by the mod TU when compiled with WH_MOD; the shim borrows it.
extern void* InternalWhModPtr;

namespace {

std::wstring Utf8ToW(const char* s) {
    if (!s || !*s) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s, -1, nullptr, 0);
    std::wstring w(n > 0 ? n - 1 : 0, L'\0');
    if (n > 0) MultiByteToWideChar(CP_UTF8, 0, s, -1, w.data(), n);
    return w;
}
std::string WToUtf8(const wchar_t* s) {
    if (!s || !*s) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, s, -1, nullptr, 0, nullptr, nullptr);
    std::string u(n > 0 ? n - 1 : 0, '\0');
    if (n > 0) WideCharToMultiByte(CP_UTF8, 0, s, -1, u.data(), n, nullptr, nullptr);
    return u;
}

const char* EnvOr(const char* name, const char* dflt) {
    const char* v = getenv(name);
    return (v && *v) ? v : dflt;
}

// ------------------------------------------------------------ log & stub bookkeeping
std::mutex g_logMutex;
// The log files are opened ONCE, lazily but before hooks can be applied in the
// common flow, and kept open: a mod that hooks CreateFileW must not see the
// engine's own logging I/O (and must not recurse through Wh_Log).
FILE* LogFile() {
    static FILE* f = [] {
        const char* path = EnvOr("AWT_MOD_LOG", nullptr);
        return path ? fopen(path, "a") : nullptr;
    }();
    return f;
}
FILE* StubFile() {
    static FILE* f = [] {
        const char* path = EnvOr("AWT_STUB_LOG", nullptr);
        return path ? fopen(path, "a") : nullptr;
    }();
    return f;
}
void WriteLine(const char* prefix, const std::wstring& text) {
    std::lock_guard<std::mutex> lk(g_logMutex);
    fwprintf(stderr, L"%hs %ls\n", prefix, text.c_str());
    fflush(stderr);
    FILE* f = LogFile();
    if (f) { fwprintf(f, L"%hs %ls\n", prefix, text.c_str()); fflush(f); }
}
void RecordStub(const char* fn) {
    std::lock_guard<std::mutex> lk(g_logMutex);
    FILE* f = StubFile();
    if (f) { fprintf(f, "%s\n", fn); fflush(f); }
}

// ------------------------------------------------------------ settings (flat JSON map)
struct Settings {
    bool loaded = false;
    std::map<std::wstring, std::wstring> strs;  // raw textual value
    std::map<std::wstring, long long> ints;
    std::map<std::wstring, bool> has;
    void Load() {
        if (loaded) return;
        loaded = true;
        const char* path = EnvOr("AWT_SETTINGS_JSON", nullptr);
        if (!path) return;
        FILE* f = fopen(path, "rb");
        if (!f) return;
        std::string js;
        char buf[4096];
        size_t n;
        while ((n = fread(buf, 1, sizeof buf, f)) > 0) js.append(buf, n);
        fclose(f);
        // minimal flat-object parser: {"key": value, ...} with value = true|false|number|"string"
        size_t i = js.find('{');
        if (i == std::string::npos) return;
        i++;
        auto skipWs = [&] { while (i < js.size() && isspace((unsigned char)js[i])) i++; };
        while (i < js.size()) {
            skipWs();
            if (js[i] == '}') break;
            if (js[i] != '"') { i++; continue; }
            size_t ke = js.find('"', ++i);
            if (ke == std::string::npos) break;
            std::string key = js.substr(i, ke - i);
            i = js.find(':', ke);
            if (i == std::string::npos) break;
            i++;
            skipWs();
            std::string val;
            if (js[i] == '"') {
                size_t ve = i + 1;
                while (ve < js.size() && !(js[ve] == '"' && js[ve - 1] != '\\')) ve++;
                val = js.substr(i + 1, ve - i - 1);
                i = ve + 1;
                std::wstring wk = Utf8ToW(key.c_str());
                strs[wk] = Utf8ToW(val.c_str());
                has[wk] = true;
            } else {
                size_t ve = i;
                while (ve < js.size() && js[ve] != ',' && js[ve] != '}') ve++;
                val = js.substr(i, ve - i);
                i = ve;
                std::wstring wk = Utf8ToW(key.c_str());
                strs[wk] = Utf8ToW(val.c_str());
                if (val == "true" || val == "false") ints[wk] = (val == "true") ? 1 : 0;
                else ints[wk] = atoll(val.c_str());
                has[wk] = true;
            }
            skipWs();
            if (i < js.size() && js[i] == ',') i++;
        }
    }
};
Settings g_settings;

// ------------------------------------------------------------ persistent storage (INI-ish)
struct Storage {
    bool loaded = false;
    std::map<std::wstring, std::pair<char, std::wstring>> vals;  // 'I'|'S'|'B'
    std::wstring path() {
        std::wstring dir = Utf8ToW(EnvOr("AWT_STORAGE_DIR", ".sandbox/storage/default"));
        CreateDirectoryW(dir.c_str(), NULL);
        return dir + L"\\values.txt";
    }
    void Load() {
        if (loaded) return;
        loaded = true;
        FILE* f = _wfopen(path().c_str(), L"rb");
        if (!f) return;
        wchar_t line[4096];
        while (fgetws(line, 4096, f)) {
            wchar_t* nl = wcschr(line, L'\n');
            if (nl) *nl = 0;
            wchar_t* tab1 = wcschr(line, L'\t');
            if (!tab1) continue;
            *tab1 = 0;
            wchar_t* tab2 = wcschr(tab1 + 1, L'\t');
            if (!tab2) continue;
            *tab2 = 0;
            vals[line] = { (char)*(tab1 + 1), tab2 + 1 };
        }
        fclose(f);
    }
    void Save() {
        FILE* f = _wfopen(path().c_str(), L"wb");
        if (!f) return;
        for (auto& kv : vals) fwprintf(f, L"%ls\t%hc\t%ls\n", kv.first.c_str(), kv.second.first, kv.second.second.c_str());
        fclose(f);
    }
};
Storage g_storage;

// ------------------------------------------------------------ hooks (MinHook)
struct PendingHook { void* target; void* hook; void** original; };
std::vector<PendingHook> g_pending;
std::vector<void*> g_applied;
bool g_mhReady = false;

bool EnsureMinHook() {
    if (!g_mhReady) {
        if (MH_Initialize() != MH_OK) return false;
        g_mhReady = true;
    }
    return true;
}

}  // namespace

// ================================================================ engine exports
extern "C" {

BOOL InternalWh_IsLogEnabled(void*) { return TRUE; }

void InternalWh_Log(void*, PCWSTR format, va_list args) {
    wchar_t buf[4096];
    _vsnwprintf(buf, 4096, format, args);
    buf[4095] = 0;
    WriteLine("[mod]", buf);
}

int InternalWh_GetIntValue(void*, PCWSTR name, int defaultValue) {
    g_storage.Load();
    auto it = g_storage.vals.find(name);
    if (it == g_storage.vals.end() || it->second.first != 'I') return defaultValue;
    return (int)_wtoi(it->second.second.c_str());
}
BOOL InternalWh_SetIntValue(void*, PCWSTR name, int value) {
    g_storage.Load();
    wchar_t buf[32];
    _snwprintf(buf, 32, L"%d", value);
    g_storage.vals[name] = { 'I', buf };
    g_storage.Save();
    return TRUE;
}
size_t InternalWh_GetStringValue(void*, PCWSTR name, PWSTR stringBuffer, size_t bufferChars) {
    g_storage.Load();
    auto it = g_storage.vals.find(name);
    if (it == g_storage.vals.end() || it->second.first != 'S') return 0;
    if (stringBuffer && bufferChars) {
        wcsncpy(stringBuffer, it->second.second.c_str(), bufferChars - 1);
        stringBuffer[bufferChars - 1] = 0;
    }
    return it->second.second.size() + 1;
}
BOOL InternalWh_SetStringValue(void*, PCWSTR name, PCWSTR value) {
    g_storage.Load();
    g_storage.vals[name] = { 'S', value ? value : L"" };
    g_storage.Save();
    return TRUE;
}
static std::wstring BinToHex(const void* p, size_t n) {
    static const wchar_t* d = L"0123456789abcdef";
    std::wstring h;
    for (size_t i = 0; i < n; ++i) {
        unsigned char c = ((const unsigned char*)p)[i];
        h += d[c >> 4]; h += d[c & 15];
    }
    return h;
}
static std::vector<unsigned char> HexToBin(const wchar_t* h) {
    std::vector<unsigned char> v;
    auto dv = [](wchar_t c) -> int {
        if (c >= L'0' && c <= L'9') return c - L'0';
        if (c >= L'a' && c <= L'f') return c - L'a' + 10;
        if (c >= L'A' && c <= L'F') return c - L'A' + 10;
        return -1;
    };
    for (; h[0] && h[1]; h += 2) {
        int a = dv(h[0]), b = dv(h[1]);
        if (a < 0 || b < 0) break;
        v.push_back((unsigned char)((a << 4) | b));
    }
    return v;
}
size_t InternalWh_GetBinaryValue(void*, PCWSTR name, void* buffer, size_t bufferSize) {
    g_storage.Load();
    auto it = g_storage.vals.find(name);
    if (it == g_storage.vals.end() || it->second.first != 'B') return 0;
    std::vector<unsigned char> bin = HexToBin(it->second.second.c_str());
    if (buffer && bufferSize) memcpy(buffer, bin.data(), bin.size() < bufferSize ? bin.size() : bufferSize);
    return bin.size();
}
BOOL InternalWh_SetBinaryValue(void*, PCWSTR name, const void* buffer, size_t bufferSize) {
    g_storage.Load();
    g_storage.vals[name] = { 'B', BinToHex(buffer, bufferSize) };
    g_storage.Save();
    return TRUE;
}
BOOL InternalWh_DeleteValue(void*, PCWSTR name) {
    g_storage.Load();
    bool had = g_storage.vals.erase(name) > 0;
    g_storage.Save();
    return had ? TRUE : FALSE;
}

BOOL InternalWh_SetDynamicSelectOption(void*, PCWSTR, PCWSTR, PCWSTR) { RecordStub("Wh_SetDynamicSelectOption"); return FALSE; }
BOOL InternalWh_DeleteDynamicSelectOption(void*, PCWSTR, PCWSTR) { RecordStub("Wh_DeleteDynamicSelectOption"); return FALSE; }
BOOL InternalWh_ClearDynamicSelectOptions(void*, PCWSTR) { RecordStub("Wh_ClearDynamicSelectOptions"); return FALSE; }

size_t InternalWh_GetModStoragePath(void*, PWSTR pathBuffer, size_t bufferChars) {
    std::wstring dir = Utf8ToW(EnvOr("AWT_STORAGE_DIR", ".sandbox/storage/default"));
    CreateDirectoryW(dir.c_str(), NULL);
    if (pathBuffer && bufferChars) {
        wcsncpy(pathBuffer, dir.c_str(), bufferChars - 1);
        pathBuffer[bufferChars - 1] = 0;
    }
    return dir.size();
}

int InternalWh_GetIntSetting(void*, PCWSTR name, va_list args) {
    g_settings.Load();
    wchar_t formatted[512];
    if (args) _vsnwprintf(formatted, 512, name, args);
    else wcsncpy(formatted, name, 511);
    formatted[511] = 0;
    auto it = g_settings.ints.find(formatted);
    if (it != g_settings.ints.end()) return (int)it->second;
    auto st = g_settings.strs.find(formatted);
    if (st != g_settings.strs.end()) return (int)_wtoi(st->second.c_str());
    return 0;  // documented default for a missing int setting is 0 (assumed, see shim-status.md)
}
PCWSTR InternalWh_GetStringSetting(void*, PCWSTR name, va_list args) {
    g_settings.Load();
    wchar_t formatted[512];
    if (args) _vsnwprintf(formatted, 512, name, args);
    else wcsncpy(formatted, name, 511);
    formatted[511] = 0;
    std::wstring val;
    auto it = g_settings.strs.find(formatted);
    if (it != g_settings.strs.end()) val = it->second;
    wchar_t* dup = (wchar_t*)malloc((val.size() + 1) * sizeof(wchar_t));
    if (!dup) return NULL;
    memcpy(dup, val.c_str(), (val.size() + 1) * sizeof(wchar_t));
    return dup;
}
void InternalWh_FreeStringSetting(void*, PCWSTR string) { free((void*)string); }

BOOL InternalWh_SetFunctionHook(void*, void* target, void* hook, void** original) {
    g_pending.push_back({ target, hook, original });
    return TRUE;
}
BOOL InternalWh_RemoveFunctionHook(void*, void* target) {
    if (!g_mhReady) return FALSE;
    return MH_RemoveHook(target) == MH_OK;
}
BOOL InternalWh_ApplyHookOperations(void*) {
    if (!EnsureMinHook()) return FALSE;
    for (auto& p : g_pending) {
        void* orig = NULL;
        if (MH_CreateHook(p.target, p.hook, &orig) != MH_OK) return FALSE;
        if (p.original) *p.original = orig;
        if (MH_EnableHook(p.target) != MH_OK) return FALSE;
        g_applied.push_back(p.target);
    }
    g_pending.clear();
    return TRUE;
}

HANDLE InternalWh_FindFirstSymbol4(void*, HMODULE, const WH_FIND_SYMBOL_OPTIONS*, WH_FIND_SYMBOL*) {
    WriteLine("[SHIM]", L"symbol lookup not available in sandbox");
    RecordStub("Wh_FindFirstSymbol");
    return NULL;
}
BOOL InternalWh_FindNextSymbol2(void*, HANDLE, WH_FIND_SYMBOL*) { RecordStub("Wh_FindNextSymbol"); return FALSE; }
void InternalWh_FindCloseSymbol(void*, HANDLE) { RecordStub("Wh_FindCloseSymbol"); }

BOOL InternalWh_HookSymbols(void*, HMODULE, const WH_SYMBOL_HOOK*, size_t, const WH_HOOK_SYMBOLS_OPTIONS*) {
    WriteLine("[SHIM]", L"symbol lookup not available in sandbox");
    RecordStub("Wh_HookSymbols");
    return FALSE;
}

BOOL InternalWh_Disasm(void*, void*, WH_DISASM_RESULT*) { RecordStub("Wh_Disasm"); return FALSE; }
const WH_URL_CONTENT* InternalWh_GetUrlContent(void*, PCWSTR url, const WH_GET_URL_CONTENT_OPTIONS*) {
    WriteLine("[SHIM]", L"Wh_GetUrlContent not implemented in sandbox");
    RecordStub("Wh_GetUrlContent");
    (void)url;
    return NULL;
}
void InternalWh_FreeUrlContent(void*, const WH_URL_CONTENT*) { RecordStub("Wh_FreeUrlContent"); }

}  // extern "C"

// ================================================================ loader glue
// Called by the host right after Wh_ModInit returns: real Windhawk applies hook
// operations queued during init automatically at that point.
extern "C" __declspec(dllexport) BOOL AwtModInitReturned(void) {
    return InternalWh_ApplyHookOperations(NULL);
}
extern "C" __declspec(dllexport) UINT AwtAppliedHookCount(void) {
    return (UINT)g_applied.size();
}

static DWORD WINAPI AwtAutoInitThread(LPVOID hinst) {
    // Wine finalises section protections of a freshly loaded image shortly after
    // DllMain returns; calling back into the image immediately from a thread born
    // inside DllMain raced with that (execute page fault). Wait it out.
    Sleep(2000);
    auto init = (BOOL(*)())GetProcAddress((HMODULE)hinst, "Wh_ModInit");
    BOOL ok = init ? init() : FALSE;
    WriteLine("[shim]", ok ? L"autoinit returned 1" : L"autoinit returned 0");
    auto applied = (BOOL(*)())GetProcAddress((HMODULE)hinst, "AwtModInitReturned");
    if (applied) applied();
    auto count = (UINT(*)())GetProcAddress((HMODULE)hinst, "AwtAppliedHookCount");
    if (count) {
        wchar_t buf[64];
        _snwprintf(buf, 64, L"hooks applied %u", count());
        WriteLine("[shim]", buf);
    }
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        InternalWhModPtr = (void*)hinst;
        DisableThreadLibraryCalls(hinst);
        if (GetEnvironmentVariableW(L"AWT_AUTOINIT", NULL, 0)) {
            HANDLE t = CreateThread(NULL, 0, AwtAutoInitThread, (LPVOID)hinst, 0, NULL);
            if (t) CloseHandle(t);
        }
    } else if (reason == DLL_PROCESS_DETACH) {
        auto pre = (void(*)())GetProcAddress(hinst, "Wh_ModBeforeUninit");
        if (pre) pre();
        auto un = (void(*)())GetProcAddress(hinst, "Wh_ModUninit");
        if (un) un();
    }
    return TRUE;
}
