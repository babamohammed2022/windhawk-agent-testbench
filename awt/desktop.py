#!/usr/bin/env python3
"""Hybrid desktop lab: scripted Win32 scene under Wine + Xvfb, with evidence.

The scene is a purpose-built Win32 program (`host/desktop.cpp`), not Explorer,
not a Windows shell, and not ReactOS code. A mod is loaded in-process through
the bench's existing shim, exactly as in `awt preview`; that is a real inline
hook in a simulated desktop process, not an injection into the Windows shell.

Every artifact is written under `.sandbox/desktop/<unique-id>/`. If a
prerequisite is missing the command reports `not_executed` (exit 3) and never
substitutes generated images for a capture.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

from . import analyzer, builder, env as host_env, metadata, png, preview, verdict

ROOT = Path(builder.REPO)
DISCLAIMER = (
    "Simulated Win32 desktop in Wine with the bench's shim. Not Explorer, not the "
    "Windows shell, not the real Windhawk engine. A capture proves that this scene "
    "changed in this process; it does not prove a mod works on real Windows."
)

# Actions are validated here before they are handed to the C++ host. The host
# has its own whitelist too; neither side executes shell commands.
PROFILES = ("win10", "basic")

ALLOWED_ACTIONS = {
    "create", "activate", "minimize", "restore", "close", "click", "key",
    "notify", "input-click", "input-key", "wait", "capture",
}
ID_RE = re.compile(r"[a-z0-9_-]{1,32}")
ACTION_MARKER_RE = re.compile(r"^\[awt-desktop\] (?P<text>.*)$")

SCENARIOS = {
    "accent-notice": {
        "profile": "win10",
        "description": (
            "Create two labelled windows, activate one, toggle a simulated "
            "notification with a synthetic mouse message, minimize the second "
            "window, close it, then capture the whole simulated desktop."
        ),
        "actions": [
            "create alpha 'Alpha window' 'Windhawk visual playground' 'First simulated client area'",
            "create beta 'Beta window' 'Beta window: second test surface' 'Second simulated client area'",
            "activate alpha",
            "click alpha notice",
            "minimize beta",
            "close beta",
            "capture",
        ],
        "assertions": [
            {"id": "two_windows", "kind": "marker", "contains": "action create id=alpha"},
            {"id": "second_window", "kind": "marker", "contains": "action create id=beta"},
            {"id": "notice_toggled", "kind": "marker",
             "contains": "action click id=alpha target=notice before=0 after=1 ok=1"},
            {"id": "beta_minimized", "kind": "marker",
             "contains": "action minimize id=beta minimized=1 ok=1"},
            {"id": "beta_closed", "kind": "marker",
             "contains": "action close id=beta closed=1 ok=1"},
            {"id": "notice_visible", "kind": "pixels", "rgb": [250, 220, 120],
             "rule": "at_least", "value": 1000},
            {"id": "accent_visible", "kind": "pixels", "rgb": [24, 95, 160],
             "rule": "at_least", "value": 10000},
            {"id": "no_crash", "kind": "no_crash"},
        ],
    },
    "taskbar-compat": {
        "profile": "win10",
        "description": (
            "Same Win10-like surface, used to observe how a real Windhawk mod "
            "behaves when loaded here: build/init refusals and errors are "
            "reported, never presented as functional success."
        ),
        "actions": [
            "create alpha 'Alpha window' 'Windhawk visual playground' 'First simulated client area'",
            "create beta 'Beta window' 'Beta window: second test surface' 'Second simulated client area'",
            "activate alpha",
            "notify on",
            "minimize beta",
            "close beta",
            "capture",
        ],
        "assertions": [
            {"id": "two_windows", "kind": "marker", "contains": "action create id=alpha"},
            {"id": "second_window", "kind": "marker", "contains": "action create id=beta"},
            {"id": "notice_toggled", "kind": "marker", "contains": "action notify state=1 ok=1"},
            {"id": "notice_visible", "kind": "pixels", "rgb": [250, 220, 120],
             "rule": "at_least", "value": 1000},
            {"id": "no_crash", "kind": "no_crash"},
        ],
    },
}


def validate_actions(lines):
    """Return normalized action lines, or raise ValueError before any execution."""
    if isinstance(lines, str):
        lines = lines.splitlines()
    lines = [line.strip() for line in lines if line and line.strip()]
    if not lines or len(lines) > 100:
        raise ValueError("a scenario needs 1 to 100 actions")
    normalized = []
    for line in lines:
        if len(line) > 200 or any(ord(c) < 32 and c != "\t" for c in line):
            raise ValueError("invalid control character or overlong action: %r" % line[:40])
        tokens = line.split()
        if tokens[0] not in ALLOWED_ACTIONS:
            raise ValueError("action not allowed: %r" % tokens[0])
        if tokens[0] in ("create", "activate", "minimize", "restore", "close", "click", "key"):
            if len(tokens) < 2 or not ID_RE.fullmatch(tokens[1]):
                raise ValueError("action needs a safe id: %r" % line)
        if tokens[0] == "create" and len(tokens) < 3:
            raise ValueError("create needs a window title")
        if tokens[0] == "key" and len(tokens) < 3:
            raise ValueError("key needs an explicit virtual-key name")
        if tokens[0] == "wait" and (len(tokens) != 2 or not tokens[1].isdigit()):
            raise ValueError("wait needs a millisecond count")
        normalized.append(line)
    return normalized


def scenario_assertions(scenario, with_mod):
    """The negative control expects the demo accent to be absent, not present."""
    result = []
    for item in SCENARIOS[scenario]["assertions"]:
        copy = dict(item)
        if item["id"] == "accent_visible" and not with_mod:
            copy.update(rule="at_most", value=100)
        result.append(copy)
    return result


def parse_desktop_output(output, mod_log=""):
    actions, markers = [], []
    for line in (output or "").splitlines():
        match = ACTION_MARKER_RE.match(line)
        if not match:
            continue
        text = match.group("text")
        if text == "capture written":
            markers.append({"capture": True, "raw": line})
            continue
        if not text.startswith("action "):
            continue
        fields = {}
        for part in text.split()[1:]:
            if "=" in part:
                key, value = part.split("=", 1)
                fields[key] = value
        entry = {"action": text.split()[1], "raw": line, **fields}
        actions.append(entry)
        markers.append(entry)
    host_markers = verdict.parse_host_markers(output, mod_log)
    errors = []
    probe, appbar, shell_emulation = [], [], None
    for line in (output or "").splitlines():
        if ("[awt-desktop] error " in line or "[awt-host] load failed" in line or
                "[awt-host] hook application failed" in line or
                "[SHIM] " in line or "unhandled-exception" in line):
            errors.append(line.strip())
        if line.startswith("[awt-desktop] probe class="):
            payload = line.split("[awt-desktop] ", 1)[1].split()
            fields = dict(part.split("=", 1) for part in payload[1:] if "=" in part)
            probe.append(fields)
        if line.startswith("[awt-desktop] appbar "):
            appbar.append(line.strip())
        if line.startswith("[awt-desktop] shell_class_emulation="):
            payload = line.split("[awt-desktop] ", 1)[1].split()
            fields = dict(part.split("=", 1) for part in payload if "=" in part)
            try:
                shell_emulation = int(fields.get("shell_class_emulation", "0"))
            except ValueError:
                shell_emulation = None
    return {
        "actions": actions,
        "markers": markers,
        "host": host_markers,
        "errors": errors,
        "probe": probe,
        "appbar": appbar,
        "shell_class_emulation": shell_emulation,
        "script_failed": "script contained a failed action" in (output or ""),
        "captured": "capture written" in (output or ""),
    }


def count_color(image, rgb):
    target = bytes(rgb)
    return sum(image.pixels[i:i + 3] == target
               for i in range(0, len(image.pixels), 3))


def evaluate_assertions(assertions, parsed, image=None):
    """Concrete checks against parsed markers and real captured pixels."""
    results = []
    for item in assertions:
        kind = item["kind"]
        if kind == "marker":
            observed = any(item["contains"] in entry["raw"] for entry in parsed["markers"])
            detail = item["contains"]
            passed = bool(observed)
        elif kind == "init":
            observed = parsed["host"]["init_returned"]
            detail = "host init marker"
            passed = observed is True
        elif kind == "no_crash":
            observed = bool(parsed["host"]["crashed"] or
                            any("unhandled-exception" in e for e in parsed["errors"]))
            detail = "no crash or unhandled-exception marker in host log"
            passed = not observed
        elif kind == "pixels":
            observed = count_color(image, item["rgb"]) if image is not None else None
            detail = "rgb=%s %s %d" % (item["rgb"], item["rule"], item["value"])
            passed = (observed is not None and
                      (observed >= item["value"] if item["rule"] == "at_least"
                       else observed <= item["value"]))
        else:
            raise ValueError("unknown assertion kind: %r" % kind)
        results.append({
            "id": item["id"], "kind": kind, "passed": bool(passed),
            "observed": observed, "expectation": detail,
        })
    return results


def describe_mod_source(mod_path):
    """Heuristic static view of a mod, available even when no build can run.

    The analyzer is textual: it can over-report strings found in comments. It is
    a prompt for what to check on real Windows, never proof that a hook ran.
    """
    source = Path(mod_path).read_text(encoding="utf-8", errors="replace")
    analysis = analyzer.analyze(source)
    out = {}
    for key in ("public_hooks", "internal_hooks", "internal_modules"):
        seen, values = set(), []
        for value in analysis.get(key) or []:
            value = str(value).strip()
            if value and value not in seen:
                seen.add(value)
                values.append(value)
            if len(values) == 12:
                break
        out[key] = values
    out["note"] = ("Textual analysis of the mod source; may include comments. "
                   "Not evidence that any hook was applied.")
    return out


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def missing_prerequisites():
    absent = []
    if sys.platform != "linux":
        absent.append("hybrid desktop currently requires Linux")
    absent.extend(builder.missing_toolchain())
    if not host_env.find_wine():
        absent.append("Wine")
    if not preview.find_wineserver(host_env.find_wine()):
        absent.append("wineserver (or AWT_WINESERVER)")
    if not shutil.which("Xvfb"):
        absent.append("Xvfb")
    return absent


def generate(mod_path=None, scenario="accent-notice", settings_override=None,
             settle_ms=500, expect_mod_effect=True, profile=None):
    """Run one scenario, always write a report, never claim more than executed."""
    if scenario not in SCENARIOS:
        raise ValueError("unknown scenario: %r" % scenario)
    if not 100 <= settle_ms <= 10000:
        raise ValueError("settle-ms must be between 100 and 10000")
    profile = profile or SCENARIOS[scenario].get("profile", "basic")
    if profile not in PROFILES:
        raise ValueError("unknown desktop profile: %r" % profile)
    actions = validate_actions(SCENARIOS[scenario]["actions"])
    output = ROOT / ".sandbox" / "desktop" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:10])
    output.mkdir(parents=True)
    with_mod = mod_path is not None
    result = {
        "desktop_schema": 1,
        "status": "not_executed",
        "scenario": scenario,
        "scenario_description": SCENARIOS[scenario]["description"],
        "profile": profile,
        "actions": actions,
        "errors": [],
        "mod": str(Path(mod_path).resolve()) if with_mod else None,
        "output": str(output),
        "disclaimer": DISCLAIMER,
        "process": {
            "name": "awt-desktop.exe",
            "explorer": False,
            "note": "A bespoke Win32 scene; its name and window classes do not "
                    "make it equivalent to explorer.exe or the Windows shell. "
                    "The host Explorer is never replaced or modified.",
        },
        "environment": host_env.probe(),
        "artifacts": {},
        "verification": {
            "verified": [],
            "simulated": [
                "desktop surface, taskbar and notification area",
                "window create/activate/minimize/close state",
                "synthetic WM_LBUTTONDOWN/WM_LBUTTONUP and WM_KEYDOWN/WM_KEYUP messages",
            ],
            "not_verifiable_here": [
                "real Explorer / Start menu / taskbar behaviour",
                "Windhawk engine injection and full lifecycle",
                "internal symbol hooks (the shim does not resolve them)",
                "real mouse and keyboard device input",
                "functional correctness on real Windows",
            ],
            "requires_real_windows": [
                "shell integration", "internal symbols", "real desktop behaviour",
            ],
        },
        "cleanup": {"performed": False, "ok": None},
    }
    report_path = output / "report.json"
    if with_mod:
        try:
            result["mod_analysis"] = describe_mod_source(mod_path)
            for symbol in result["mod_analysis"]["internal_hooks"]:
                result["verification"]["not_verifiable_here"].append(
                    "internal-symbols: %s" % symbol)
            for module in result["mod_analysis"]["internal_modules"]:
                result["verification"]["not_verifiable_here"].append(
                    "internal-module: %s" % module)
        except OSError as exc:
            result["errors"].append("mod source not readable: %s" % exc)
    try:
        absent = missing_prerequisites()
        if absent:
            result["reason"] = "missing prerequisites: " + ", ".join(absent) + "; no scene was started"
            return result
        version = subprocess.run([host_env.find_wine(), "--version"], capture_output=True,
                                 text=True, timeout=10)
        result["wine_version"] = version.stdout.strip()

        build = None
        if with_mod:
            build = builder.build(mod_path, sandbox=str(output), settings_override=settings_override)
            result["build"] = {k: build[k] for k in ("ok", "errors", "warnings", "settings")}
            if not build["ok"]:
                result.update(status="build_failed", reason="mod compilation failed; no capture substituted")
                return result
            result["verification"]["verified"].append("mod DLL built with the bench shim")
        executable = builder.build_desktop(sandbox=str(output))
        result["binaries"] = {"desktop_sha256": _hash(executable)}
        if build:
            result["binaries"]["mod_sha256"] = _hash(build["dll"])

        actions_file = output / "actions.txt"
        actions_file.write_text("\n".join(actions) + "\n", encoding="utf-8")
        result["artifacts"]["actions"] = str(actions_file)

        env = {k: v for k, v in os.environ.items() if not k.startswith(("AWT_", "WINE"))}
        prefix = output / "prefix"
        env.update(WINEPREFIX=str(prefix), WINEARCH="win64", WINEDEBUG="-all",
                   WINEDLLOVERRIDES="winemenubuilder.exe=d")
        log = output / "host.log"
        mod_log = output / "mod.log"
        stubs_log = output / "stubs.log"
        for path in (log, mod_log, stubs_log):
            path.touch()
        (output / "storage").mkdir()
        env.update(
            AWT_MOD_LOG=preview.windows_path(mod_log),
            AWT_STUB_LOG=preview.windows_path(stubs_log),
            AWT_SETTINGS_JSON=preview.windows_path(build["settings_json"]) if build else "",
            AWT_STORAGE_DIR=preview.windows_path(output / "storage") + "\\",
        )
        wine = host_env.find_wine()
        wineserver = preview.find_wineserver(wine)
        cleanup_log = output / "cleanup.log"
        with preview.display_session(output / "xvfb.log") as display:
            env["DISPLAY"] = display
            try:
                # First invocation initializes a fresh prefix and disables the debugger there.
                code, timed_out = preview._command(
                    [wine, "reg", "add", r"HKCU\Software\Wine\Debug", "/v", "CrashHandler",
                     "/d", "", "/f"], env, output / "prefix.log", 90)
                if timed_out or code != 0:
                    result.update(status="run_failed", reason="Wine prefix initialization failed")
                    return result
                command = [wine, preview.windows_path(executable),
                           "--capture", preview.windows_path(output / "capture.bmp"),
                           "--actions", preview.windows_path(actions_file),
                           "--profile", profile,
                           "--settle-ms", str(settle_ms)]
                if build:
                    command.extend(["--mod", preview.windows_path(build["dll"])])
                code, timed_out = preview._command(command, env, log, 90)
                host_text = log.read_text(encoding="utf-8", errors="replace")
                mod_text = mod_log.read_text(encoding="utf-8", errors="replace")
                parsed = parse_desktop_output(host_text, mod_text)
                result["host"] = parsed["host"]
                result["actions_observed"] = parsed["actions"]
                result["errors"] = parsed["errors"]
                result["shell_probe"] = parsed["probe"]
                result["shell_class_emulation"] = parsed["shell_class_emulation"]
                result["appbar"] = parsed["appbar"]
                result["artifacts"].update(
                    host_log=str(log), mod_log=str(mod_log), stubs_log=str(stubs_log))
                stubs_text = stubs_log.read_text(encoding="utf-8", errors="replace")
                if "symbol lookup not available" in stubs_text + mod_text:
                    result["shim_limits"] = ["internal symbol lookup not available in sandbox"]
                    if "internal symbol lookup not available in sandbox" not in \
                            result["verification"]["not_verifiable_here"]:
                        result["verification"]["not_verifiable_here"].append(
                            "internal symbol lookup not available in sandbox")
                result["host"]["timed_out"] = timed_out
                result["host"]["exit_code"] = code
                if timed_out or code != 0 or not parsed["captured"] or parsed["script_failed"]:
                    reason = ("desktop run did not complete cleanly: exit=%s timed_out=%s "
                              "capture=%s script_failed=%s"
                              % (code, timed_out, parsed["captured"], parsed["script_failed"]))
                    result.update(status="run_failed", reason=reason)
                    return result
                if parsed["host"]["crashed"]:
                    result.update(status="run_failed", reason="crash markers in the host log")
                    return result
                if build and parsed["host"]["init_returned"] is not True:
                    result.update(status="run_failed",
                                  reason="mod init did not return true in the simulated desktop")
                    return result
                result["verification"]["verified"].extend([
                    "scripted desktop actions executed in the Wine process",
                    "real Win32 client capture written by the desktop host",
                ])
                if build:
                    result["verification"]["verified"].append("mod init returned true in the scene")

                bmp = output / "capture.bmp"
                if not bmp.is_file() or bmp.stat().st_size > 54 + 2048 * 1536 * 4:
                    raise ValueError("capture missing or exceeds the size limit")
                image = preview.decode_capture(bmp.read_bytes())
                if (image.width, image.height) != (960, 600):
                    result.update(status="run_failed",
                                  reason="unexpected capture geometry %dx%d" %
                                         (image.width, image.height))
                    return result
                png.write(output / "capture.png", image)
                result["artifacts"]["screenshot"] = str(output / "capture.png")
                result["artifacts"]["capture_bmp"] = str(bmp)
                assertions = evaluate_assertions(
                    scenario_assertions(scenario, with_mod and expect_mod_effect), parsed, image)
                result["assertions"] = assertions
                failed = [item["id"] for item in assertions if not item["passed"]]
                if failed:
                    result.update(status="assertion_failed",
                                  reason="failed observable assertions: " + ", ".join(failed))
                    return result
                result.update(status="passed",
                              reason="all scripted actions, capture and concrete assertions passed")
                return result
            finally:
                # Only this run's fresh prefix and wineserver: no global pkill.
                code, timed_out = preview._command([wineserver, "-k"], env, cleanup_log, 10)
                if not timed_out and code == 0:
                    code, timed_out = preview._command([wineserver, "-w"], env, cleanup_log, 10)
                ok = code == 0 and not timed_out
                result["cleanup"] = {"performed": True, "ok": ok, "log": str(cleanup_log)}
                if not ok:
                    result.update(status="run_failed",
                                  reason="Wineserver cleanup did not complete; prefix retained")
                elif prefix.is_dir():
                    shutil.rmtree(prefix, ignore_errors=True)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        result.update(status="error", reason="%s: %s" % (type(exc).__name__, exc))
        return result
    finally:
        report_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="awt desktop")
    parser.add_argument("mod", nargs="?", help="mod to load (omit for a control run)")
    parser.add_argument("--scenario", default="accent-notice", choices=sorted(SCENARIOS))
    parser.add_argument("--profile", choices=PROFILES, default=None,
                        help="surface profile; the scenario default is win10")
    parser.add_argument("--no-mod", action="store_true", help="control run without a mod")
    parser.add_argument("--no-mod-effect", action="store_true",
                        help="keep the mod loaded but expect no visible accent (disabled setting)")
    parser.add_argument("--settings-json")
    parser.add_argument("--settings", action="append", default=[])
    parser.add_argument("--settle-ms", type=int, default=500)
    args = parser.parse_args(argv)
    if args.mod and args.no_mod:
        parser.error("use either a mod path or --no-mod, not both")
    if not args.mod and not args.no_mod:
        parser.error("provide a mod path or --no-mod for the control scene")
    try:
        from .cli import _load_settings_override
        override = _load_settings_override(args.settings_json, args.settings)
        result = generate(None if args.no_mod else args.mod, args.scenario, override,
                          args.settle_ms,
                          expect_mod_effect=not args.no_mod and not args.no_mod_effect,
                          profile=args.profile)
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    print("desktop: %s — %s" % (result["status"], result.get("reason", "")))
    print("report: %s" % (Path(result["output"]) / "report.json"))
    if result.get("artifacts", {}).get("screenshot"):
        print("screenshot: %s" % result["artifacts"]["screenshot"])
    return 0 if result["status"] == "passed" else 3 if result["status"] == "not_executed" else 1


if __name__ == "__main__":
    sys.exit(main())
