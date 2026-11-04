"""Run a built mod under headless Wine and collect the evidence for the report."""
import json
import os
import re
import shutil
import signal
import subprocess
import time

from . import analyzer, builder, report
from . import env as host_env

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SANDBOX = os.path.join(REPO, ".sandbox")
WINE = os.environ.get("AWT_WINE", "/usr/lib/wine/wine64")
if shutil.which("wine64"):
    WINE = "wine64"
elif shutil.which("wine"):
    WINE = "wine"


def _free_display():
    for d in range(90, 99):
        if not os.path.exists("/tmp/.X11-unix/X%d" % d):
            return d
    return 99


def _auto_mode(meta):
    incs = [i.lower() for i in meta["include"]]
    if any("explorer" in i for i in incs):
        return "self", None      # M5b upgrades explorer includes to wine-native probing
    return "inject", "testapp"


INJECT_FALLBACK_REASON = (
    "mod could not be loaded into the remote target on this Wine build: "
    "CreateRemoteThread+LoadLibraryW maps the image with a non-executable first "
    ".text page (execute page fault) and AppInit_DLLs is not honoured; "
    "fell back to the simulated in-process host (see docs/limitations.md)")


def _execute(cmd, env, run_log, seconds, screenshot=False):
    timed_out = False
    shot = None
    with open(run_log, "w") as lf:
        proc = subprocess.Popen(cmd, env=env, stdout=lf, stderr=subprocess.STDOUT)
        try:
            if screenshot:
                time.sleep(max(2.0, seconds * 0.6))
                shot = run_log + ".png"
                subprocess.run(["import", "-window", "root", shot],
                               capture_output=True, timeout=20)
            proc.wait(timeout=seconds + 60)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.send_signal(signal.SIGKILL)
    return proc, timed_out, shot


def run(mod_path, seconds=6, mode="auto", target=None, screenshot=False,
        symbols=None):
    started = time.time()
    b = builder.build(mod_path)
    source = open(mod_path, encoding="utf-8", errors="replace").read()
    ana = analyzer.analyze(source)
    meta = b["meta"]

    rep = report.new_report(b, ana)
    rep["environment"] = host_env.probe()
    if symbols:
        # Feature 1 result, attached additively: it never changes the verdict
        # level, because a symbol existing in a PDB is not an execution.
        report.attach_symbol_matrix(rep, symbols)
    rep["run"]["backend"] = "wine"
    logs_dir = os.path.join(SANDBOX, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    os.makedirs(os.path.join(SANDBOX, "storage", b["mod_id"]), exist_ok=True)
    hist = os.path.join(SANDBOX, "history")
    os.makedirs(hist, exist_ok=True)

    if not b["ok"]:
        missing = b.get("toolchain_missing") or []
        if missing:
            # Nothing was compiled and nothing was executed: say exactly that
            # instead of reporting a build failure that never happened.
            reason = ("no execution backend available in this environment: %s; "
                      "the mod was neither compiled nor run" % ", ".join(missing))
            rep["verdict"]["level"] = "not_executed"
            rep["verdict"]["not_executed_reason"] = reason
            rep["run"]["backend"] = "unavailable"
            rep["run"]["reason"] = reason
            rep["verdict"]["summary"] = "level=not_executed; %s" % reason
        else:
            rep["verdict"]["level"] = "build_failed"
            rep["verdict"]["summary"] = "build failed: %d error(s)" % len(b["errors"])
        return report.finish(rep, hist)

    # ---- include analysis (section 3.3, v1: honest simulation mapping)
    if mode == "auto":
        mode, auto_target = _auto_mode(meta)
        target = target or auto_target
    target_exe = None
    if mode == "inject":
        tname = target or "testapp"
        target_exe = builder.build_target(tname)

    display = _free_display()
    xvfb = subprocess.Popen(["Xvfb", ":%d" % display, "-screen", "0", "1280x720x24"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.2)

    # ---- M5b: probe Wine's own explorer (desktop mode) for explorer.exe includes
    probe = {"alive": False, "window_classes_found": [], "window_classes_missing": []}
    explorer_procs, trayapp_proc = [], None
    env0 = dict(os.environ)
    env0.update({"DISPLAY": ":%d" % display,
                 "WINEPREFIX": os.path.join(SANDBOX, "prefix"),
                 "WINEDEBUG": "-all"})
    wants_explorer = any("explorer" in i.lower() for i in meta["include"])
    if wants_explorer:
        try:
            explorer_procs.append(subprocess.Popen(
                [WINE, "explorer.exe", "/desktop=awt,1280x720"],
                env=env0, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            time.sleep(4)
            enum_exe = builder.build_target("enumwins")
            eout = subprocess.run([WINE, "Z:" + enum_exe.replace("/", "\\")],
                                  env=env0, capture_output=True, text=True, timeout=30).stdout
            classes = []
            for l in eout.splitlines():
                m2 = re.search(r"class=(\S+)", l)
                if m2 and m2.group(1):
                    classes.append(m2.group(1))
            probe["alive"] = True
            probe["window_classes_found"] = sorted({c for c in classes if c})
            probe["window_classes_missing"] = [c for c in ("Shell_TrayWnd",) if c not in classes]
        except Exception as exc:  # probe must never kill the run
            probe["alive"] = False
            probe["window_classes_missing"] = ["Shell_TrayWnd (%s)" % exc]
        if "system-tray" in analyzer.analyze(source)["subjects"]:
            try:
                tray_exe = builder.build_target("trayapp")
                trayapp_proc = subprocess.Popen(
                    [WINE, "Z:" + tray_exe.replace("/", "\\")],
                    env=env0, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                trayapp_proc = None
    for entry in analyzer.include_subjects(source, meta["include"]):
        low = entry["include"].lower()
        if "control" in low:
            ctrl = os.path.join(SANDBOX, "prefix", "drive_c", "windows", "system32", "control.exe")
            present = os.path.exists(ctrl)
            used = "wine control.exe (probe)" if present else "testapp.exe"
            reason = ("control.exe present in this Wine build and probed, but the mod "
                      "cannot be loaded into it here (injection limitations, "
                      "docs/limitations.md); simulated host used"
                      if present else "control.exe not present in this Wine build; "
                                      "simulated host used instead")
            rep["include_analysis"].append({
                "include": entry["include"], "subject": entry["subject"],
                "target_used": used, "mode": "simulated",
                "probe": {"alive": present, "window_classes_found": [],
                          "window_classes_missing": [] if present else ["control.exe window"]},
                "injection_method": "in-process LoadLibraryW",
                "fallback_reason": reason,
            })
            continue
        if "explorer" in low:
            used, method, reason = "awt-host(self)", "in-process LoadLibraryW", \
                ("wine explorer probed (desktop mode, classes=%s) but the mod cannot be "
                 "loaded into it on this Wine build (remote LoadLibraryW page-protection "
                 "bug, AppInit_DLLs not honoured); public-API behaviour exercised in the "
                 "simulated host instead" % ",".join(probe["window_classes_found"])[:120])
        else:
            used, method, reason = "testapp.exe", "CreateRemoteThread+LoadLibraryW", \
                "no Wine-native program matches this include; sandbox testapp used as hook target"
        rep["include_analysis"].append({
            "include": entry["include"],
            "subject": entry["subject"],
            "target_used": used,
            "mode": "simulated",
            "probe": probe if "explorer" in low else {"alive": True, "window_classes_found": [], "window_classes_missing": []},
            "injection_method": method,
            "fallback_reason": reason,
        })
    rep["_fallback_reason"] = None

    run_log = os.path.join(logs_dir, "run-%d.log" % int(started))
    mod_log = os.path.join(logs_dir, "mod-%d.log" % int(started))
    stub_log = os.path.join(logs_dir, "stub-%d.log" % int(started))
    for pth in (mod_log, stub_log):
        open(pth, "w").close()

    host = builder.build_host()
    env = dict(os.environ)
    env.update({
        "DISPLAY": ":%d" % display,
        "WINEPREFIX": os.path.join(SANDBOX, "prefix"),
        "WINEDEBUG": "-all",
        "AWT_MOD_LOG": mod_log.replace("/", "\\"),
        "AWT_STUB_LOG": stub_log.replace("/", "\\"),
        "AWT_SETTINGS_JSON": b["settings_json"].replace("/", "\\"),
        "AWT_STORAGE_DIR": (os.path.join(SANDBOX, "storage", b["mod_id"]) + os.sep).replace("/", "\\"),
    })
    # the shim opens its log files through Wine: give it the unix path too via Z: drive
    env["AWT_MOD_LOG"] = "Z:" + mod_log.replace("/", "\\")
    env["AWT_STUB_LOG"] = "Z:" + stub_log.replace("/", "\\")
    env["AWT_SETTINGS_JSON"] = "Z:" + b["settings_json"].replace("/", "\\")
    env["AWT_STORAGE_DIR"] = "Z:" + (os.path.join(SANDBOX, "storage", b["mod_id"]) + os.sep).replace("/", "\\")

    cmd = [WINE, host, "--mod", "Z:" + b["dll"].replace("/", "\\"),
           "--mode", mode, "--seconds", str(seconds)]
    if target_exe:
        cmd += ["--target", "Z:" + target_exe.replace("/", "\\")]
    proc, timed_out, shot = _execute(cmd, env, run_log, seconds, screenshot)
    for p_ in explorer_procs:
        p_.terminate()
    if trayapp_proc:
        trayapp_proc.terminate()
    out0 = open(run_log, errors="replace").read()
    fallback_reason = None
    if mode == "inject" and "autoinit returned" not in open(mod_log, errors="replace").read():
        fallback_reason = INJECT_FALLBACK_REASON
        mode = "self"
        cmd = [WINE, host, "--mod", "Z:" + b["dll"].replace("/", "\\"),
               "--mode", "self", "--seconds", str(seconds)]
        env.pop("AWT_AUTOINIT", None)
        run_log = run_log.replace(".log", "-self.log")
        mod_log2 = mod_log.replace(".log", "-self.log")
        open(mod_log2, "w").close()
        env["AWT_MOD_LOG"] = "Z:" + mod_log2.replace("/", "\\")
        proc, timed_out, shot = _execute(cmd, env, run_log, seconds, False)
        mod_log = mod_log2
    xvfb.terminate()
    subprocess.run(["pkill", "-f", "winedbg"], capture_output=True)

    out = open(run_log, errors="replace").read()
    modlog = open(mod_log, errors="replace").read() if os.path.exists(mod_log) else ""
    stublog = open(stub_log, errors="replace").read() if os.path.exists(stub_log) else ""

    # ---- parse host markers
    m = re.search(r"\[awt-host\] load failed err=(\d+)", out)
    load_failed = bool(m)
    m = re.search(r"\[awt-host\] init returned (-?\d+)", out) or \
        re.search(r"autoinit returned (\d+)", modlog)
    init_ret = int(m.group(1)) if m else None
    m = re.search(r"\[awt-host\] hooks applied count (\d+)", out) or \
        re.search(r"hooks applied (\d+)", modlog)
    hooks_applied = int(m.group(1)) if m else 0
    if init_ret != 1:
        hooks_applied = 0
    m = re.search(r"\[awt-host\] target exit code (\d+)", out)
    target_exit = int(m.group(1)) if m else None
    crashed = (re.search(r"unhandled (exception|page fault)", out, re.I) is not None or
               re.search(r"wine:.*crash", out, re.I) is not None or
               (target_exit is not None and target_exit >= 0xC0000000))
    exit_code = proc.returncode if not timed_out else None

    hooks_fired = {}
    for name in ana["public_hooks"]:
        n = len(re.findall(re.escape(name) + r"[:\(]", modlog))
        if n:
            hooks_fired[name] = n

    if fallback_reason:
        rep["_fallback_reason"] = fallback_reason
        for entry in rep["include_analysis"]:
            if "control" in entry["include"].lower():
                continue  # keeps its own probe-specific reason
            entry["mode"] = "simulated"
            entry["fallback_reason"] = fallback_reason
            entry["target_used"] = "awt-host(self)"
            entry["injection_method"] = "in-process LoadLibraryW"
    rep["run"].update({
        "backend": "wine",
        "target": "awt-host.exe (self)" if mode == "self" else target,
        "init_returned": (init_ret == 1) if init_ret is not None else None,
        "hooks_applied": hooks_applied,
        "hooks_fired": hooks_fired,
        "crashed": crashed,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "log": [l.rstrip("\r\n") for l in modlog.splitlines()][:200],
    })
    rep["shim"]["not_implemented_called"] = sorted(set(stublog.split()))
    if ana["internal_hooks"] or ana["internal_modules"]:
        rep["shim"]["simulated_with_limits"] += ["Wh_FindFirstSymbol", "Wh_HookSymbols"]

    # ---- verdict
    if load_failed or init_ret in (0, 2, 3, 5, 6, 7):
        rep["verdict"]["level"] = "load_failed"
    elif crashed:
        rep["verdict"]["level"] = "crashed"
    elif hooks_fired:
        rep["verdict"]["level"] = "public_hooks_verified"
    else:
        rep["verdict"]["level"] = "loads_no_crash"
    verified = ["build"]
    if not load_failed and init_ret == 1:
        verified.append("load")
    if hooks_fired:
        verified.append("public API hooks")
    rep["verdict"]["verified"] = verified
    not_verifiable = []
    for s in ana["internal_hooks"]:
        not_verifiable.append("internal-symbols: %s" % s)
    for mod in ana["internal_modules"]:
        not_verifiable.append("internal-module: %s" % mod)
    rep["verdict"]["not_verifiable_here"] = sorted(set(not_verifiable))
    rep["verdict"]["requires_real_windows"] = bool(not_verifiable)
    rep["verdict"]["summary"] = (
        "level=%s; verified=%s; not verifiable here=%s" %
        (rep["verdict"]["level"], ", ".join(verified) or "nothing",
         ", ".join(sorted(set(not_verifiable))) or "nothing"))
    rep["artifacts"]["wine_log"] = run_log
    if shot and os.path.exists(shot):
        rep["artifacts"]["screenshots"] = [shot]
    return report.finish(rep, hist)
