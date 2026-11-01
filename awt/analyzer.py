"""Classify what a mod touches: public Win32 hooks vs internal Windows symbols."""
import re

INTERNAL_MODULE_HINTS = (
    "twinui.pcshell.dll", "twinui.dll", "taskbar.dll", "startui.dll", "startmenu.dll",
    "explorer.exe", "shellexperiencehost", "searchui", "xaml", "windows.ui.xaml",
    "ctfmon", "shellcommon",
)
INTERNAL_SYMBOL_PATTERNS = (
    re.compile(r"HookSymbols\s*\("),
    re.compile(r"Wh_FindFirstSymbol"),
    re.compile(r"SYMBOL_HOOK"),
    re.compile(r"__cdecl\s+\w+::"),           # mangled MSVC member functions
    re.compile(r"public:\s+virtual"),
)
TRAY_SIGNALS = (
    "Shell_TrayWnd", "TrayNotifyWnd", "Shell_NotifyIcon", "NOTIFYICONDATA",
    "TrayClockWClass", "SysPager", "ITrayNotify", "NotifyIconOverflowWindow",
    "TaskbarCreated",
)
CONTROL_SIGNALS = (
    "control.exe", "Control_RunDLL", ".cpl", "control panel",
)
# exports of DLLs Wine implements well enough to exercise
KNOWN_PUBLIC = {
    "CreateFileW", "ReadFile", "WriteFile", "CloseHandle", "GetProcAddress",
    "LoadLibraryW", "SendMessageW", "PostMessageW", "SetWindowPos", "ShowWindow",
    "CreateWindowExW", "RegisterClassExW", "SetWindowsHookExW", "CallNextHookEx",
    "GetWindowRect", "SetWindowRgn", "DwmExtendFrameIntoClientArea",
    "Shell_NotifyIconW", "ImageList_Create", "RegOpenKeyExW", "RegQueryValueExW",
    "GetSystemMetrics", "SystemParametersInfoW", "DrawTextW", "CreateFontW",
}

HOOK_CALL = re.compile(r"(?:WindhawkUtils::)?(?:Wh_)?SetFunctionHook(?:<[^>]*>)?\s*\(\s*([A-Za-z_]\w*)")


def _strip_metadata(source: str) -> str:
    import re as _re
    return _re.sub(r"//\s*==WindhawkMod==.*?//\s*==/WindhawkMod==", "", source, flags=_re.S)


def analyze(source: str) -> dict:
    source = _strip_metadata(source)
    public, internal, unknown = [], [], []
    for m in HOOK_CALL.finditer(source):
        name = m.group(1)
        if name in ("target", "targetFunction"):
            continue
        if name in KNOWN_PUBLIC or re.fullmatch(r"[A-Z][A-Za-z0-9_]*[AW]?", name):
            public.append(name)
        else:
            unknown.append(name)
    internal_symbols = []
    for pat in INTERNAL_SYMBOL_PATTERNS:
        for m in pat.finditer(source):
            internal_symbols.append(m.group(0).strip())
    # decorated symbol strings passed to HookSymbols / SYMBOL_HOOK tables
    for m in re.finditer(r'LR?"\((.*?)\)"', source, re.S):
        internal_symbols.append(m.group(1)[:80])
    modules = sorted({h for h in INTERNAL_MODULE_HINTS if h in source.lower()})
    if internal_symbols or modules:
        internal += sorted(set(internal_symbols))
    subjects = []
    if any(s in source for s in TRAY_SIGNALS):
        subjects.append("system-tray")
    if any(s.lower() in source.lower() for s in CONTROL_SIGNALS):
        subjects.append("control-panel")
    return {
        "public_hooks": sorted(set(public)),
        "internal_hooks": internal,
        "unknown_hooks": sorted(set(unknown)),
        "internal_modules": modules,
        "subjects": subjects,
    }


def include_subjects(source: str, includes) -> list:
    """Per-include subject tags used by the target mapper (section 3.3)."""
    base = analyze(source)["subjects"]
    out = []
    for inc in includes:
        tags = list(base)
        low = inc.lower()
        if "control" in low:
            tags.append("control-panel")
        out.append({"include": inc, "subject": sorted(set(tags))})
    return out
