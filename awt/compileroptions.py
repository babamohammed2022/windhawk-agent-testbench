"""Classify mod-declared ``@compilerOptions`` before they reach the compiler.

Windhawk mods routinely declare ``-lcomctl32``, ``-std=c++20``, ``-DFOO=1`` and
occasionally ``-Wl,...``. Passing arbitrary tokens straight to a cross compiler
either drops them silently or fails with a message that does not say which
metadata flag was at fault. This module makes the decision explicit: accepted
flags are really used by the builder, rejected flags are reported with a reason,
and nothing is claimed as linked or resolved unless a build actually happened.

It is a small allowlist, not a shell: no token is executed, and no token is
expanded. A library outside the known set is still accepted (MinGW may provide
it), but it is marked as unverified until the linker has spoken.
"""
import re

SAFE_LIB_NAME = re.compile(r"^[A-Za-z0-9_+.]+$")
SAFE_DEFINE = re.compile(r"^-D[A-Za-z_][A-Za-z0-9_]*(?:=.*)?$")
SAFE_STD = re.compile(r"^-std=(c\+\+|gnu\+\+)(98|11|14|17|20|23|2[6-9])$")
SAFE_WL = {
    "--enable-auto-import", "--disable-auto-import", "--enable-runtime-pseudo-reloc",
    "--large-address-aware", "--stack", "--heap", "--subsystem", "--dynamicbase",
    "--no-seh", "--nxcompat",
}

# Import libraries commonly declared by real Windhawk mods. Unknown names are
# not rejected; they are flagged as unverified instead of being guessed at.
KNOWN_LIBS = {
    "kernel32", "user32", "gdi32", "advapi32", "shell32", "shlwapi", "ole32",
    "oleaut32", "comctl32", "comdlg32", "gdiplus", "dwmapi", "uxtheme",
    "winhttp", "wininet", "runtimeobject", "crypt32", "psapi", "version",
    "imm32", "msimg32", "odbc32", "opengl32", "setupapi", "userenv",
    # Import libraries used by real shell-recreation mods (e.g. the Windows 7
    # network flyout): standard MinGW provides all of these.
    "iphlpapi", "wlanapi", "uuid", "comdlg32", "oleacc", "propsys", "shcore",
    "windowscodecs", "ntdll", "msvcrt",
}


def classify(tokens):
    """Split raw metadata tokens into accepted and explicitly rejected flags."""
    result = {
        "raw": list(tokens or []),
        "accepted": [], "rejected": [], "libraries": [], "defines": [],
        "standards": [], "link_flags": [], "unverified_libraries": [],
        "note": ("Accepted flags are passed to the real compiler; rejected flags are "
                 "reported and never passed. Acceptance does not prove that a "
                 "library exists until a build actually succeeds."),
    }
    for token in result["raw"]:
        if not isinstance(token, str) or not token:
            result["rejected"].append({"flag": str(token),
                                       "reason": "empty or non-text compiler option"})
            continue
        if token.startswith("-l") and len(token) > 2:
            name = token[2:]
            if not SAFE_LIB_NAME.match(name):
                result["rejected"].append({"flag": token, "reason":
                                           "unsafe library name; refused"})
                continue
            result["accepted"].append(token)
            result["libraries"].append(name)
            if name not in KNOWN_LIBS:
                result["unverified_libraries"].append(name)
        elif SAFE_DEFINE.match(token):
            result["accepted"].append(token)
            result["defines"].append(token)
        elif SAFE_STD.match(token):
            result["accepted"].append(token)
            result["standards"].append(token)
        elif token.startswith("-Wl,"):
            parts = token[4:].split(",")
            if all(p.split("=", 1)[0] in SAFE_WL for p in parts if p):
                result["accepted"].append(token)
                result["link_flags"].append(token)
            else:
                result["rejected"].append({"flag": token, "reason":
                                           "linker flag outside the audited allowlist"})
        else:
            result["rejected"].append({"flag": token, "reason":
                                       "not an audited -l<lib>, -D<name>, -std=c++XX "
                                       "or safe -Wl,... option"})
    return result


def compile_flags(opts):
    """Flags that affect compiling the mod translation unit."""
    return list(opts.get("defines") or [])


def link_flags(opts):
    """Flags that affect linking the mod DLL."""
    out = []
    for flag in opts.get("accepted") or []:
        if flag not in (opts.get("defines") or []) and flag not in (opts.get("standards") or []):
            out.append(flag)
    return out
