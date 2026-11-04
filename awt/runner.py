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
from . import verdict

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

    # ---- parse host markers (shared with the native Windows backend so both
    #      reach the same verdict from the same output: awt/verdict.py)
    markers = verdict.parse_host_markers(out, modlog)
    load_failed = markers["load_failed"]
    init_ret = markers["init_returned_code"]
    hooks_applied = markers["hooks_applied"]
    target_exit = markers["target_exit_code"]
    crashed = markers["crashed"]
    exit_code = proc.returncode if not timed_out else None
    hooks_fired = verdict.count_hooks_fired(modlog, ana["public_hooks"])

    if fallback_reason:
        rep["_fallback_reason"] = fallback_reason
        for entry in rep["include_analysis"]:
            if "control" in entry["include"].lower():
                continue  # keeps its own probe-specific reason
            entry["mode"] = "simulated"
            entry["fallback_reason"] = fallback_reason
            entry["target_used"] = "awt-host(self)"
            entry["injection_method"] = "in-process LoadLibraryW"
    verdict.apply_run_result(
        rep, ana, markers, hooks_fired, exit_code=exit_code, timed_out=timed_out,
        run_log=run_log, screenshot=(shot if shot and os.path.exists(shot) else None),
        stub_log=stublog, mode=mode,
        target="awt-host.exe (self)" if mode == "self" else target)
    rep["run"]["backend"] = "wine"
    rep["run"]["log"] = [l.rstrip("\r\n") for l in modlog.splitlines()][:200]
    return report.finish(rep, hist)
