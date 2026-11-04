"""Compile a .wh.cpp mod into a Windows DLL linked against the shim (+ MinHook)."""
import json
import os
import re
import shutil
import subprocess

from . import env, metadata

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def _tool(env_name, cross, native_candidates):
    """Compiler/binutil name: explicit override, cross prefix, or native.

    On Linux the bench cross-compiles with `x86_64-w64-mingw32-*`. On Windows the
    same MinGW toolchain is usually installed without the prefix (MSYS2 mingw64,
    or the mingw package a CI runner provides), so the native names are tried
    too. `AWT_CXX` and friends always win: a user with a non-standard toolchain
    should not have to edit this file.
    """
    override = os.environ.get(env_name)
    if override:
        return override
    if os.name != "nt":
        return cross
    for candidate in native_candidates:
        if env.which([candidate]):
            return candidate
    return cross


CC = _tool("AWT_CXX", "x86_64-w64-mingw32-g++", ["g++", "clang++"])
CC_C = _tool("AWT_CC", "x86_64-w64-mingw32-gcc", ["gcc", "clang"])
NM = _tool("AWT_NM", "x86_64-w64-mingw32-nm", ["nm", "llvm-nm"])
FILT = _tool("AWT_CXXFILT", "x86_64-w64-mingw32-c++filt",
             ["c++filt", "llvm-cxxfilt"])

BASE_LIBS = ["-lkernel32", "-luser32", "-lgdi32", "-ladvapi32", "-lshell32",
             "-lshlwapi", "-lole32", "-lcomctl32"]

LIFECYCLE = {
    "Wh_ModInit()": "Wh_ModInit",
    "Wh_ModUninit()": "Wh_ModUninit",
    "Wh_ModAfterInit()": "Wh_ModAfterInit",
    "Wh_ModBeforeUninit()": "Wh_ModBeforeUninit",
    "Wh_ModSettingsChanged()": "Wh_ModSettingsChanged",
    "Wh_ModSettingsChanged(bool*)": "Wh_ModSettingsChangedReload",
}

ERR_RE = re.compile(r"^(?P<file>[^:\s][^:]*):(?P<line>\d+):(?P<col>\d+):\s+(?P<kind>error|warning|fatal error):\s+(?P<msg>.+)$")


def _run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def missing_toolchain() -> list:
    """Cross-compilation tools that are not installed (empty when ready).

    Checked, never assumed: without them no build is possible and the report
    must say `not_executed` instead of pretending the mod is broken.
    """
    missing = []
    for label, names in (("MinGW C++ compiler", [CC, "x86_64-w64-mingw32-clang++"]),
                         ("MinGW C compiler", [CC_C, "x86_64-w64-mingw32-gcc"]),
                         ("MinGW nm", [NM, "x86_64-w64-mingw32-nm"]),
                         ("MinGW c++filt", [FILT, "x86_64-w64-mingw32-c++filt"])):
        if not env.which(names):
            missing.append(label)
    return missing


def _parse_diagnostics(text):
    errors, warnings = [], []
    for line in text.splitlines():
        m = ERR_RE.match(line.strip())
        if not m:
            continue
        entry = {"file": m.group("file"), "line": int(m.group("line")),
                 "col": int(m.group("col")), "message": m.group("msg").strip()}
        (errors if m.group("kind") != "warning" else warnings).append(entry)
    return errors, warnings


def _mangled_lifecycle(obj):
    nm = _run([NM, "--defined-only", obj])
    syms = [l.split()[-1] for l in nm.stdout.splitlines() if l.strip()]
    filt = _run([FILT], cwd=None) if False else subprocess.run(
        [FILT], input="\n".join(syms), capture_output=True, text=True)
    out = {}
    for mangled, demangled in zip(syms, filt.stdout.splitlines()):
        if demangled.strip() in LIFECYCLE:
            out[LIFECYCLE[demangled.strip()]] = mangled
    return out


def build(mod_path, sandbox=None):
    mod_path = os.path.abspath(mod_path)
    sandbox = sandbox or os.path.join(REPO, ".sandbox")
    source = open(mod_path, encoding="utf-8", errors="replace").read()
    meta = metadata.parse_metadata(source)
    settings = metadata.parse_settings(source)
    mod_id = meta["raw"].get("id", os.path.splitext(os.path.basename(mod_path))[0])
    bdir = os.path.join(sandbox, "build", mod_id)
    os.makedirs(bdir, exist_ok=True)

    result = {"ok": False, "errors": [], "warnings": [], "dll": None,
              "mod_id": mod_id, "meta": meta, "settings": settings,
              "build_dir": bdir, "toolchain_missing": []}
    missing = missing_toolchain()
    if missing:
        result["toolchain_missing"] = missing
        result["errors"].append({
            "file": mod_path, "line": 0, "col": 0,
            "message": ("build not attempted: %s not installed in this "
                        "environment (run ./setup.sh where packages are "
                        "reachable); nothing was compiled or executed"
                        % ", ".join(missing))})
        return result
    settings_path = os.path.join(bdir, "settings.json")
    with open(settings_path, "w", encoding="utf-8") as f:
        f.write(metadata.settings_json(settings))
    result["settings_json"] = settings_path

    mod_o = os.path.join(bdir, "mod.o")
    cmd = [CC, "-c", "-std=c++20", "-O1",
           "-I", os.path.join(REPO, "shim", "include"), "-include", "awt_compat.h",
           "-DWH_MOD", "-DUNICODE", "-D_UNICODE",
           '-DWH_MOD_ID=L"%s"' % mod_id,
           '-DWH_MOD_VERSION=L"%s"' % meta["raw"].get("version", "0.0.0"),
           "-I", os.path.join(REPO, "shim", "include"),
           "-o", mod_o, mod_path]
    p = _run(cmd)
    errs, warns = _parse_diagnostics(p.stderr)
    result["errors"] += errs
    result["warnings"] += warns
    if p.returncode != 0:
        if not errs:
            result["errors"].append({"file": mod_path, "line": 0, "col": 0,
                                     "message": p.stderr.strip()[:2000]})
        return result

    exports = _mangled_lifecycle(mod_o)
    if "Wh_ModInit" not in exports:
        result["errors"].append({"file": mod_path, "line": 0, "col": 0,
                                 "message": "mod does not define BOOL Wh_ModInit()"})
        return result
    defp = os.path.join(bdir, "exports.def")
    with open(defp, "w") as f:
        f.write("LIBRARY %s.dll\nEXPORTS\n" % mod_id)
        for plain, mangled in exports.items():
            f.write("    %s = %s\n" % (plain, mangled))

    objs = [mod_o]
    shim_o = os.path.join(bdir, "shim.o")
    p = _run([CC, "-c", "-std=c++20", "-O1", "-DUNICODE", "-D_UNICODE",
              "-I", os.path.join(REPO, "shim", "include"),
              "-I", os.path.join(REPO, "third_party", "minhook", "include"),
              "-o", shim_o, os.path.join(REPO, "shim", "shim.cpp")])
    e2, w2 = _parse_diagnostics(p.stderr)
    result["errors"] += e2
    result["warnings"] += w2
    if p.returncode != 0:
        return result
    objs.append(shim_o)

    mh_src = os.path.join(REPO, "third_party", "minhook", "src")
    for rel in ("buffer.c", "hook.c", "trampoline.c", os.path.join("hde", "hde64.c")):
        o = os.path.join(bdir, os.path.basename(rel).replace(".c", ".mho"))
        p = _run([CC_C, "-c", "-O2", "-I", os.path.join(mh_src, "..", "include"),
                  "-o", o, os.path.join(mh_src, rel)])
        if p.returncode != 0:
            result["errors"].append({"file": rel, "line": 0, "col": 0,
                                     "message": p.stderr.strip()[:1000]})
            return result
        objs.append(o)

    dll = os.path.join(bdir, mod_id + ".dll")
    cmd = [CC, "-shared", "-o", dll] + objs + [defp] + BASE_LIBS + meta["compilerOptions"] + \
          ["-static-libgcc", "-static-libstdc++", "-lmsvcrt"]
    p = _run(cmd)
    e3, w3 = _parse_diagnostics(p.stderr)
    result["errors"] += e3
    result["warnings"] += w3
    if p.returncode != 0 or not os.path.exists(dll):
        if not result["errors"]:
            result["errors"].append({"file": "link", "line": 0, "col": 0,
                                     "message": p.stderr.strip()[:2000]})
        return result
    result["ok"] = True
    result["dll"] = dll
    result["exports"] = exports
    return result


def build_target(name):
    """Compile one of targets/*.cpp into .sandbox/build/<name>.exe."""
    out = os.path.join(REPO, ".sandbox", "build", name + ".exe")
    src = os.path.join(REPO, "targets", name + ".cpp")
    if os.path.exists(out) and os.path.getmtime(out) > os.path.getmtime(src):
        return out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    p = _run([CC, "-std=c++20", "-O1", "-DUNICODE", "-D_UNICODE", "-o", out, src,
              "-lkernel32", "-luser32", "-ladvapi32", "-lgdi32", "-lshell32",
              "-static-libgcc", "-static-libstdc++"])
    if p.returncode != 0:
        raise RuntimeError("target build failed: " + p.stderr[:2000])
    return out


def build_host():
    """Compile the loader/host exe once per sandbox."""
    host = os.path.join(SANDBOX if False else os.path.join(REPO, ".sandbox"), "build", "awt-host.exe")
    os.makedirs(os.path.dirname(host), exist_ok=True)
    if os.path.exists(host):
        return host
    src = os.path.join(REPO, "host", "host.cpp")
    p = _run([CC, "-std=c++20", "-O1", "-DUNICODE", "-D_UNICODE", "-municode", "-o", host, src,
              "-lkernel32", "-luser32", "-ladvapi32", "-static-libgcc", "-static-libstdc++"])
    if p.returncode != 0:
        raise RuntimeError("host build failed: " + p.stderr[:2000])
    return host
