"""Detect which Windows shell surfaces a mod touches, and say what can be shown.

This is static, evidence-bearing analysis of a mod's own source and metadata.
It never runs the mod and never claims that a surface works: for each surface it
reports whether the bench has a stand-in, whether behaviour would be simulated,
or whether it is not verifiable without real Windows. The output is advisory;
it cannot change `verdict.level`.

The patterns were chosen from the shells this bench already models
(`host/desktop.cpp`, `targets/trayapp.exe`) and from common categories of
published Windhawk mods. Unicode/base64 resources are excluded from matching:
a resource blob that happens to contain "DUI" is not a DUI dependency.
"""
import re

from . import targeting

REPO = __file__
SURFACES = (
    {
        "id": "start_menu",
        "pattern": r"StartMenuExperienceHost|ShellExperienceHost|SearchHost\.exe|"
                   r"Windows\.UI\.Xaml|twinui\.pcshell\.dll|\bStartMenu::",
        "stand_in": None,
        "coverage": "not_verifiable",
        "reason": ("the XAML/WinRT Start host and its private symbols are absent "
                   "under Wine; the offline symbol matrix can check names only"),
    },
    {
        "id": "taskbar",
        "pattern": r"Shell_TrayWnd|TrayNotifyWnd|MSTaskListWClass|ReBarWindow32|"
                   r"ToolbarWindow32|TaskbarCreated|TaskbarCreated|Shell_TrayWnd",
        "stand_in": "awt-desktop.exe",
        "coverage": "simulated",
        "reason": ("the Win10-like desktop scene draws taskbar pixels with public "
                   "GDI calls; it is not Explorer and has no real taskbar state"),
    },
    {
        "id": "tray_icon",
        "pattern": r"Shell_NotifyIcon|NOTIFYICONDATA|NotifyIconOverflowWindow",
        "stand_in": "trayapp.exe",
        "coverage": "simulated",
        "reason": "NIM_ADD/MODIFY/DELETE in a test client, not the Windows tray",
    },
    {
        "id": "menus",
        "pattern": r"TrackPopupMenu(?:Ex)?|CreatePopupMenu|WM_CONTEXTMENU|IContextMenu|"
                   r"InsertMenuW|TPM_RETURNCMD",
        "stand_in": "awt-desktop.exe",
        "coverage": "simulated",
        "reason": "the scripted scene can trigger synthetic WM_* menu messages; "
                  "no Explorer context menu is involved",
    },
    {
        "id": "tooltips",
        "pattern": r"TOOLTIP_CLASS|TOOLINFO|TTM_(?:ADDTOOL|SETTOOL|TRACKACTIVATE)|"
                   r"TrackToolTips",
        "stand_in": "awt-desktop.exe",
        "coverage": "simulated",
        "reason": "tooltip classes are created, if at all, only inside the stand-in process",
    },
    {
        "id": "common_dialogs",
        "pattern": r"IFileDialog|GetOpenFileName|GetSaveFileName|COMDLG32|"
                   r"SHBrowseForFolder|IFileSaveDialog",
        "stand_in": None,
        "coverage": "not_verifiable",
        "reason": "no target in this bench opens a common dialog; a hook would be "
                  "unexercised rather than proven good",
    },
    {
        "id": "dwm",
        "pattern": r"Dwm(?:SetWindowAttribute|ExtendFrameIntoClientArea|"
                   r"GetWindowAttribute|IsCompositionEnabled|EnableComposition)",
        "stand_in": "awt-desktop.exe",
        "coverage": "simulated",
        "reason": "Wine's DWM surface is not the Windows DWM; at most a forward call",
    },
    {
        "id": "network_shell",
        "pattern": r"INetworkListManager|CLSID_NetworkListManager|Wlan(?:OpenHandle|"
                   r"EnumInterfaces|GetConnectionList)|IPHLPAPI|network flyout",
        "stand_in": None,
        "coverage": "not_verifiable",
        "reason": "WLAN/network-list COM objects and the network flyout UI are not "
                  "provided by this bench",
    },
    {
        "id": "dark_mode_undocumented",
        "pattern": r"dui\.dll|SetPreferredAppMode|AllowDarkModeForWindow|"
                   r"FlushMenuThemes|ShouldAppsUseDarkMode|MAKEINTRESOURCEA\(1[0-9]{2}\)",
        "stand_in": None,
        "coverage": "not_verifiable",
        "reason": ("undocumented uxtheme/dui ordinals change between Windows builds; "
                   "the bench cannot prove that an ordinal is correct"),
    },
    {
        "id": "control_panel",
        "pattern": r"control\.exe|Control_RunDLL|\.cpl\b",
        "stand_in": "testapp.exe",
        "coverage": "simulated",
        "reason": "Wine's control.exe is probed but the mod cannot be loaded into it",
    },
    {
        "id": "resources_download",
        "pattern": r"Wh_GetUrlContent|URLDownloadToFile|raw\.githubusercontent\.com|"
                   r"WinHttp(?:Open|ReadData|ReceiveResponse)",
        "stand_in": None,
        "coverage": "not_verifiable",
        "reason": "runtime resource downloads are not part of the sandbox evidence",
    },
)

PLAN = {
    "dark_mode_undocumented": ("confirm each ordinal/undocumented export against "
                               "the exact Windows build on real Windows"),
    "start_menu": ("run `python3 -m awt.symbols <mod> --json .sandbox/symbols.json` "
                   "for each Windows build, then verify hook installation on a "
                   "real client Windows VM"),
    "taskbar": ("run `python3 -m awt.desktop <mod> --scenario taskbar-compat` and "
                "read assertions/screenshots as stand-in observations only"),
    "tray_icon": ("run/shim-conformance: NIM_ADD/MODIFY/DELETE in trayapp.exe; "
                  "actual Explorer tray behaviour needs Windows"),
    "menus": "open the target menu on real Windows; synthetic WM_* messages are not menus",
    "tooltips": "verify hover text and timing on real Windows",
    "common_dialogs": "drive a real Open/Save dialog on Windows with the mod loaded",
    "dwm": "compare DWM attributes and frames on a real Windows client build",
    "network_shell": "check WLAN/NLM state and flyout placement on real Windows",
    "control_panel": "probe control.exe here; verify the applet on real Windows",
    "resources_download": "pre-fetch resources or supply them locally; network fetches are not evidence",
}

# Window class names and DLL names usually appear only in string literals,
# so keep them. Resource blobs are long single lines and are skipped instead:
# a base64 string containing "DUI" is not a DUI dependency.
MAX_LINE = 240


def _lines(source, regex):
    out = []
    for number, line in enumerate(source.splitlines(), 1):
        if regex.search(line):
            out.append({"line": number, "text": line.strip()[:160]})
            if len(out) >= 4:
                break
    return out


def analyze(source, meta=None, analysis=None):
    """Return detected shell surfaces with explicit, honest coverage."""
    meta = meta or {}
    includes = " ".join(meta.get("include") or [])
    code = "\n".join(line for line in (source or "").splitlines()
                      if len(line) <= MAX_LINE)
    # Metadata is advisory evidence: a mod named "taskbar ..." may touch the
    # taskbar even when its implementation only uses public window APIs.
    metadata_text = " ".join(str(meta.get("raw", {}).get(k, ""))
                             for k in ("id", "name", "description"))
    meta_hints = {
        "taskbar": r"taskbar",
        "menus": r"\bmenu\b|menus",
        "start_menu": r"start menu|start-menu",
        "network_shell": r"network flyout|network center",
        "control_panel": r"control panel",
    }
    detected, plan = [], []
    for surface in SURFACES:
        regex = re.compile(surface["pattern"], re.I)
        evidence = _lines(code, regex)
        if not evidence and surface["id"] in ("start_menu", "control_panel", "network_shell"):
            evidence = _lines(includes, regex)
        if not evidence and surface["id"] in meta_hints:
            evidence = _lines(metadata_text, re.compile(meta_hints[surface["id"]], re.I))
        if not evidence:
            continue
        detected.append({
            "id": surface["id"],
            "coverage": surface["coverage"],
            "stand_in": surface["stand_in"],
            "reason": surface["reason"],
            "evidence": evidence,
        })
        plan.append(PLAN[surface["id"]])
    counts = {}
    for item in detected:
        counts[item["coverage"]] = counts.get(item["coverage"], 0) + 1
    return {
        "schema": 1,
        "surfaces": detected,
        "summary": {"detected": len(detected), "by_coverage": counts},
        "verification_plan": plan,
        "target_repercussion": (targeting.select(meta, analysis) if meta else None),
        "note": ("Static detection from the mod's own source. A detected surface "
                 "is not proof of behaviour; simulated means a stand-in may "
                 "exercise similar public APIs, not_verifiable means Windows "
                 "Windhawk is needed. Never changes verdict.level."),
    }
