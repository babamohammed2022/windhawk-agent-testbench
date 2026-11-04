"""Native Windows execution: the CI backend.

This is the backend that answers the bench's central limitation honestly: the
mod is compiled and executed by a real Windows process, on a real Windows
installation. Two things it does *not* do, stated up front:

* the mod is still loaded through the bench's own shim host (`host/host.cpp`),
  not through the Windhawk engine, so a mod that only resolves internal symbols
  at runtime stays `not_verifiable_here` - see `docs/limitations.md`;
* GitHub-hosted runners are **Windows Server** (`windows-latest` is build 20348),
  and Windows Server has no immersive taskbar, no Start menu experience and a
  different explorer. Shell-related outcomes from this backend are therefore
  marked *indicative* in `verdict.indicative`, never *verified*.

Every detection helper below is a pure function over strings/dicts so it can be
unit-tested on the Linux machine where this bench is developed; only
`_rtl_get_version()` and `_read_registry()` need to be on Windows, and both
degrade to `None`/`{}` elsewhere.
"""
import os
import platform
import re
import subprocess
import sys
import time

from .. import analyzer, builder, report as report_mod, verdict
from .. import env as host_env
from .base import (Backend, apply_environment, environment_block, exit_code_for,
                   indicative_notes, shell_profile)


def _history_dir():
    """Where `report.finish` files a copy of every run (same place as Wine)."""
    path = os.path.join(host_env.SANDBOX, "history")
    os.makedirs(path, exist_ok=True)
    return path

GITHUB_RUNNER_NOTE = (
    "executed on a GitHub-hosted runner: the machine is a Microsoft-managed VM "
    "image, not a user desktop, and its Windows Server shell makes shell-related "
    "outcomes indicative only")

SHIM_HOST_NOTE = (
    "the mod was loaded by the bench's own shim host, not by the Windhawk engine: "
    "public API hooks are exercised for real, internal symbol hooks are still "
    "not_verifiable_here")

# build number -> marketing name. Used only for the human-readable `edition`
# field; the authoritative value is `build_number` itself.
BUILD_EDITIONS = {
    7600: "Windows 7 / Server 2008 R2",
    7601: "Windows 7 SP1 / Server 2008 R2 SP1",
    9200: "Windows 8 / Server 2012",
    9600: "Windows 8.1 / Server 2012 R2",
    10240: "Windows 10 1507",
    10586: "Windows 10 1511",
    14393: "Windows 10 1607 / Server 2016",
    15063: "Windows 10 1703",
    16299: "Windows 10 1709",
    17134: "Windows 10 1803",
    17763: "Windows 10 1809 / Server 2019",
    18362: "Windows 10 1903",
    18363: "Windows 10 1909",
    19041: "Windows 10 2004",
    19042: "Windows 10 20H2",
    19043: "Windows 10 21H1",
    19044: "Windows 10 21H2",
    19045: "Windows 10 22H2",
    20348: "Windows Server 2022",
    22000: "Windows 11 21H2",
    22621: "Windows 11 22H2",
    22631: "Windows 11 23H2",
    26100: "Windows 11 24H2 / Server 2025",
    26200: "Windows 11 25H2",
}

PRODUCT_TYPES = {1: "client", 2: "domain-controller", 3: "server"}


VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?")


def parse_windows_ver(text):
    """`Microsoft Windows [Version 10.0.20348.1234]` -> build number + version.

    Accepts the two forms Windows actually produces: the `ver` command output
    (which mentions Windows) and the registry `CurrentVersion` value (which is
    nothing but the version). Anything else returns None - a version-looking
    string from another operating system must not become a Windows build number.
    """
    text = (text or "").strip()
    if not text:
        return None
    match = VERSION_RE.fullmatch(text)
    if not match and "windows" in text.lower():
        match = VERSION_RE.search(text)
    if not match:
        return None
    major, minor, build = int(match.group(1)), int(match.group(2)), int(match.group(3))
    return {"os_version": "%d.%d.%s" % (major, minor, build),
            "build_number": build,
            "revision": int(match.group(4)) if match.group(4) else None}


def product_type_from_registry(value):
    """Windows `ProductType` registry value -> 1 (client) / 2 (DC) / 3 (server)."""
    mapping = {"winnt": 1, "lanmannt": 3, "lanmanserver": 3, "server": 3,
               "client": 1, "workstation": 1, "domain-controller": 2,
               "domaincontroller": 2}
    if isinstance(value, int):
        return value if value in PRODUCT_TYPES else None
    if value is None:
        return None
    return mapping.get(str(value).strip().lower().replace("_", ""))


def installation_type_to_profile(value):
    """`InstallationType` (Client / Server / Server Core) -> shell profile hint."""
    text = str(value or "").strip().lower()
    if text.startswith("server"):
        return "server"
    if text == "client":
        return "client"
    return None


def edition_for(build_number, product_name=None, display_version=None):
    """Human-readable edition. `ProductName` wins when Windows provides one."""
    if product_name:
        text = str(product_name).strip()
        if display_version and display_version not in text:
            text = "%s %s" % (text, display_version)
        return text
    try:
        build = int(build_number)
    except (TypeError, ValueError):
        return None
    label = BUILD_EDITIONS.get(build)
    if label and display_version:
        return "%s (%s)" % (label, display_version)
    return label


def github_runner_info(env=None):
    """What the GitHub Actions environment says about this machine, if anything."""
    env = os.environ if env is None else env
    is_github = str(env.get("GITHUB_ACTIONS", "")).lower() == "true"
    info = {
        "is_github": is_github,
        "runner_os": env.get("RUNNER_OS") or None,
        "runner_name": env.get("RUNNER_NAME") or None,
        "image_os": env.get("ImageOS") or None,
        "image_version": env.get("ImageVersion") or None,
        "repository": env.get("GITHUB_REPOSITORY") or None,
        "run_id": env.get("GITHUB_RUN_ID") or None,
        "server_url": env.get("GITHUB_SERVER_URL") or None,
    }
    return info if is_github or any(v for v in info.values() if v is not None) else {}


def _rtl_get_version():
    """`RtlGetVersion` via ctypes: the build number Windows itself reports.

    `GetVersionEx` lies without a manifest, `RtlGetVersion` does not. Returns
    None off Windows or if anything at all goes wrong.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes

        class OSVERSIONINFOEXW(ctypes.Structure):
            _fields_ = [("dwOSVersionInfoSize", ctypes.c_ulong),
                        ("dwMajorVersion", ctypes.c_ulong),
                        ("dwMinorVersion", ctypes.c_ulong),
                        ("dwBuildNumber", ctypes.c_ulong),
                        ("dwPlatformId", ctypes.c_ulong),
                        ("szCSDVersion", ctypes.c_wchar * 128),
                        ("wServicePackMajor", ctypes.c_ushort),
                        ("wServicePackMinor", ctypes.c_ushort),
                        ("wSuiteMask", ctypes.c_ushort),
                        ("wProductType", ctypes.c_ubyte),
                        ("wReserved", ctypes.c_ubyte)]

        ntdll = ctypes.WinDLL("ntdll")
        kernel32 = ctypes.WinDLL("kernel32")
        info = OSVERSIONINFOEXW()
        info.dwOSVersionInfoSize = ctypes.sizeof(info)
        if ntdll.RtlGetVersion(ctypes.byref(info)) != 0:
            return None
        product_type = info.wProductType
        if not product_type:
            # RtlGetVersion on some builds leaves wProductType empty
            GetProductType = kernel32.GetProductType
            value = ctypes.c_ubyte(0)
            if GetProductType(ctypes.byref(value)):
                product_type = value.value
        return {"build_number": int(info.dwBuildNumber),
                "major": int(info.dwMajorVersion),
                "minor": int(info.dwMinorVersion),
                "product_type": int(product_type) if product_type else None,
                "os_version": "%d.%d.%d" % (info.dwMajorVersion, info.dwMinorVersion,
                                            info.dwBuildNumber)}
    except Exception:                                          # pragma: no cover
        return None


def _read_registry():
    """`HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion` as a plain dict."""
    if os.name != "nt":
        return {}
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
        wanted = ("ProductName", "DisplayVersion", "ReleaseId", "CurrentBuild",
                  "CurrentBuildNumber", "CurrentMajorVersionNumber",
                  "CurrentMinorVersionNumber", "InstallationType", "EditionID",
                  "CompositionEditionID")
        out = {}
        for name in wanted:
            try:
                out[name] = winreg.QueryValueEx(key, name)[0]
            except OSError:
                continue
        winreg.CloseKey(key)
        return out
    except Exception:                                          # pragma: no cover
        return {}


def detect_environment(env=None, collected_by="awt.backends.windows_ci"):
    """Everything this process can honestly say about where it is running."""
    registry = _read_registry()
    version = _rtl_get_version()
    runner = github_runner_info(env)
    notes = []

    build_number = None
    os_version = None
    product_type = None
    collected_from = []
    if version:
        build_number = version["build_number"]
        os_version = version["os_version"]
        product_type = version["product_type"]
        collected_from.append("RtlGetVersion")
    if build_number is None:
        for key in ("CurrentBuildNumber", "CurrentBuild"):
            if registry.get(key):
                try:
                    build_number = int(registry[key])
                    collected_from.append("registry:%s" % key)
                    break
                except (TypeError, ValueError):
                    continue
    if product_type is None:
        product_type = product_type_from_registry(registry.get("InstallationType"))
        if product_type:
            collected_from.append("registry:InstallationType")

    profile_hint = installation_type_to_profile(registry.get("InstallationType"))
    profile = shell_profile(os_name=(platform.system() if os.name == "nt" else None),
                            product_type=product_type, build_number=build_number)
    if profile == "unknown" and profile_hint:
        profile = profile_hint
    if os.name != "nt":
        profile = "unknown"
        notes.append("not collected on Windows: this process runs on %s, so no "
                     "Windows environment was probed" % platform.system())
    if not collected_from:
        notes.append("no Windows version source answered (RtlGetVersion and the "
                     "registry were both unavailable)")
    caveats = [SHIM_HOST_NOTE]
    if runner.get("is_github"):
        caveats.append(GITHUB_RUNNER_NOTE)

    return environment_block(
        backend="windows-runner",
        os=platform.system() if os.name == "nt" else None,
        os_version=os_version,
        edition=edition_for(build_number, registry.get("ProductName"),
                            registry.get("DisplayVersion") or registry.get("ReleaseId")),
        build_number=build_number,
        product_type=PRODUCT_TYPES.get(product_type) if product_type else None,
        shell_profile=profile,
        virtualization=("github-hosted runner VM" if runner.get("is_github")
                        else None),
        vm_used=bool(runner.get("is_github")),
        snapshot_restored=None,
        runner=runner.get("runner_name"),
        runner_image=(runner.get("image_os") and
                      "%s %s" % (runner["image_os"], runner.get("image_version") or ""))
                     or runner.get("image_os"),
        arch=platform.machine() or None,
        hostname=None,
        collected_by=collected_by,
        collected_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        seconds=None,
        notes=notes + (["collected from %s" % ", ".join(collected_from)]
                       if collected_from else []),
        caveats=caveats,
    )


class WindowsRunnerBackend(Backend):
    """Compile and run a mod in a native Windows process (CI or a local PC)."""

    name = "windows-runner"
    label = "native Windows (GitHub Actions runner or any Windows host)"
    executes_on = "windows"

    def describe(self):
        env = self.environment()
        return {
            "name": self.name,
            "label": self.label,
            "executes_on": self.executes_on,
            "shell_profile": env.get("shell_profile"),
            "limitations": [
                SHIM_HOST_NOTE,
                "GitHub-hosted runners are Windows Server: shell-related outcomes "
                "are indicative, not verified",
                "no remote injection into explorer.exe/twinui: the bench runs the "
                "mod in its own host process",
                "screenshots need a real interactive session; a service session "
                "yields a blank image and is reported as unavailable",
            ],
        }

    def available(self):
        if os.name != "nt":
            return False, ("not running on Windows (this process is %s/%s): the "
                           "windows-runner backend can only execute inside a "
                           "native Windows job" % (platform.system(),
                                                   platform.machine()))
        missing = builder.missing_toolchain()
        if missing:
            return False, ("no native MinGW toolchain: %s (install MSYS2 mingw64 "
                           "or set AWT_CXX/AWT_CC/AWT_NM/AWT_CXXFILT)"
                           % ", ".join(missing))
        env = self.environment()
        return True, "native Windows %s (build %s, shell profile %s)" % (
            env.get("edition") or env.get("os_version") or "?",
            env.get("build_number") or "?", env.get("shell_profile"))

    def environment(self):
        return detect_environment()

    # -- screenshots --------------------------------------------------------
    PS_SCREENSHOT = (
        "$ErrorActionPreference = 'Stop';"
        "Add-Type -AssemblyName System.Windows.Forms;"
        "Add-Type -AssemblyName System.Drawing;"
        "$b = [System.Windows.Forms.SystemInformation]::VirtualScreen;"
        "$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height;"
        "$g = [System.Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size);"
        "$bmp.Save($env:AWT_SHOT_PATH,"
        " [System.Drawing.Imaging.ImageFormat]::Png);"
        "$g.Dispose(); $bmp.Dispose();"
        "Write-Output \"$($b.Width)x$($b.Height)\"")

    def take_screenshot(self, path, timeout=90, shell="powershell"):
        """(path or None, note). A failure is reported, never faked."""
        if os.name != "nt":
            return None, "screenshot unavailable: needs a native Windows session"
        run_env = dict(os.environ, AWT_SHOT_PATH=path)
        exe = shell if host_env.which([shell]) else (
            "powershell.exe" if host_env.which(["powershell.exe"]) else None)
        if not exe:
            return None, "screenshot unavailable: no PowerShell on PATH"
        try:
            proc = subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-Command",
                 self.PS_SCREENSHOT],
                capture_output=True, text=True, timeout=timeout, env=run_env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return None, "screenshot unavailable: %s" % exc
        if proc.returncode != 0 or not os.path.exists(path):
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            return None, "screenshot unavailable: %s" % (
                detail[-1][:200] if detail else "PowerShell exit %d, no file written"
                % proc.returncode)
        size = os.path.getsize(path)
        if size < 200:
            return None, ("screenshot unavailable: %d bytes written, too small to "
                          "be an image" % size)
        note = "captured %s (%d bytes) with PowerShell CopyFromScreen" % (
            (proc.stdout or "").strip() or "?", size)
        if _looks_blank(path):
            note += ("; WARNING the image is a single colour: no shell was drawn, "
                     "which is what a service/session-0 desktop looks like - treat "
                     "this screenshot as unavailable evidence")
        return path, note

    # -- execution ----------------------------------------------------------
    def run(self, mod_path, seconds=6, mode="auto", target=None,
            screenshot=False, symbols=None, timeout_slack=120, **kwargs):
        env = self.environment()
        ok, reason = self.available()
        hist = _history_dir()
        if not ok:
            rep = self.not_executed_report(mod_path, reason, env)
            return report_mod.finish(rep, hist)

        started = time.time()
        b = builder.build(mod_path)
        ana = analyzer.analyze(mod_path)
        rep = report_mod.new_report(b, ana)
        rep["environment"] = host_env.probe()
        apply_environment(rep, env, backend=self.name)
        if not b["ok"]:
            rep["verdict"]["level"] = "build_failed"
            rep["verdict"]["summary"] = ("level=build_failed; %d compile error(s)"
                                         % len(b["errors"]))
            rep["run"]["reason"] = "the mod did not compile on Windows"
            return report_mod.finish(rep, hist)

        dll = b.get("dll")
        if not dll or not os.path.exists(dll):
            rep["verdict"]["level"] = "not_executed"
            reason = ("the build reported success but produced no mod DLL - the "
                      "mod was not executed")
            rep["verdict"]["not_executed_reason"] = reason
            rep["run"]["reason"] = reason
            rep["verdict"]["summary"] = "level=not_executed; %s" % reason
            return report_mod.finish(rep, hist)
        try:
            host_exe = builder.build_host()
        except OSError as exc:
            host_exe = None
            rep["build"]["errors"].append(
                {"file": "host/host.cpp", "line": 0, "col": 0,
                 "message": "host.exe could not be built: %s" % exc})
        if not host_exe or not os.path.exists(host_exe):
            rep["verdict"]["level"] = "not_executed"
            rep["verdict"]["not_executed_reason"] = (
                "the bench's host.exe could not be built on this Windows machine")
            rep["run"]["reason"] = rep["verdict"]["not_executed_reason"]
            rep["verdict"]["summary"] = "level=not_executed; %s" % rep["run"]["reason"]
            return report_mod.finish(rep, hist)

        if target is None:
            target = "awt-host.exe (self)"
        logs = os.path.join(host_env.SANDBOX, "logs")
        os.makedirs(logs, exist_ok=True)
        mod_log = os.path.join(logs, "mod-native.log")
        stub_log = os.path.join(logs, "stub-native.log")
        for path in (mod_log, stub_log):
            if os.path.exists(path):
                os.unlink(path)
        storage = os.path.join(host_env.SANDBOX, "storage", b["mod_id"]) + os.sep
        os.makedirs(storage, exist_ok=True)
        # The shim reads these as native Windows paths: no "Z:" drive mapping is
        # needed here, which is the one thing this backend does differently from
        # the Wine runner.
        run_env = dict(os.environ,
                       AWT_MOD_LOG=mod_log.replace("/", "\\"),
                       AWT_STUB_LOG=stub_log.replace("/", "\\"),
                       AWT_SETTINGS_JSON=(b.get("settings_json") or "{}").replace("/", "\\"),
                       AWT_STORAGE_DIR=storage.replace("/", "\\"))
        cmd = [host_exe, "--mod", dll, "--mode", mode, "--seconds", str(int(seconds))]
        out = ""
        exit_code = None
        timed_out = False
        run_log = os.path.join(logs, "native-run.log")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=int(seconds) + timeout_slack,
                                  env=run_env, cwd=os.path.dirname(dll))
            out = (proc.stdout or "") + (proc.stderr or "")
            exit_code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            out = ((exc.stdout or "") + (exc.stderr or "")) if isinstance(
                exc.stdout, str) else ""
            out += "\n[awt] native run timed out after %ds" % (
                int(seconds) + timeout_slack)
        except OSError as exc:
            rep["verdict"]["level"] = "not_executed"
            rep["verdict"]["not_executed_reason"] = "could not start host.exe: %s" % exc
            rep["run"]["reason"] = rep["verdict"]["not_executed_reason"]
            rep["verdict"]["summary"] = "level=not_executed; %s" % rep["run"]["reason"]
            return report_mod.finish(rep, hist)
        try:
            with open(run_log, "w", encoding="utf-8") as f:
                f.write("cmd: %s\n" % " ".join(cmd))
                f.write(out)
        except OSError:
            run_log = None

        modlog = _read_text(mod_log)
        stublog = _read_text(stub_log)
        markers = verdict.parse_host_markers(out, modlog)
        hooks_fired = verdict.count_hooks_fired(modlog, ana["public_hooks"])
        shot_path = None
        shot_note = None
        if screenshot:
            wanted = os.path.join(logs, "screenshot-native-%d.png" % int(time.time()))
            shot_path, shot_note = self.take_screenshot(wanted)

        verdict.apply_run_result(
            rep, ana, markers, hooks_fired, exit_code=exit_code,
            timed_out=timed_out, run_log=run_log, screenshot=shot_path,
            stub_log=stublog, mode=mode, target=target)
        rep["run"]["log"] = [line.rstrip("\r\n")
                             for line in modlog.splitlines()][:200]
        rep["run"]["seconds"] = round(time.time() - started, 2)
        environment = dict(env)
        environment["seconds"] = rep["run"]["seconds"]
        environment["caveats"] = list(env.get("caveats") or [])
        environment["notes"] = list(env.get("notes") or []) + (
            [shot_note] if shot_note else [])
        apply_environment(rep, environment, backend=self.name)
        return report_mod.finish(rep, hist)


def _read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _looks_blank(path):
    """True when a screenshot is a single colour (a service desktop, no shell).

    Uses Pillow when it happens to be installed; without it the question is left
    unanswered (None) rather than guessed.
    """
    try:
        from PIL import Image, ImageStat
    except Exception:
        return None
    try:
        with Image.open(path) as image:
            stat = ImageStat.Stat(image.convert("L"))
            return bool(stat.stddev and stat.stddev[0] < 0.01)
    except Exception:                                          # pragma: no cover
        return None


def summarise_markdown(rep):
    """A GitHub step summary for a report produced by this backend.

    It states the level, where the run happened, and every caveat - including the
    one that matters most on CI: `windows-latest` is Windows Server, so anything
    about the shell is indicative.
    """
    verdict_block = rep.get("verdict") or {}
    run = rep.get("run") or {}
    env = run.get("environment") or {}
    mod = rep.get("mod") or {}
    level = verdict_block.get("level") or "?"
    lines = ["## Native Windows run: `%s`" % level, ""]
    lines.append("| field | value |")
    lines.append("| --- | --- |")
    rows = [
        ("mod", mod.get("id") or "?"),
        ("backend", run.get("backend") or "?"),
        ("os", env.get("os") or "not collected"),
        ("edition", env.get("edition") or "not collected"),
        ("build", env.get("build_number") or "not collected"),
        ("shell profile", env.get("shell_profile") or "unknown"),
        ("VM used", env.get("vm_used")),
        ("runner image", env.get("runner_image") or env.get("runner") or "-"),
        ("verified", ", ".join(verdict_block.get("verified") or []) or "nothing"),
        ("not verifiable here",
         ", ".join(verdict_block.get("not_verifiable_here") or []) or "nothing"),
        ("exit code", run.get("exit_code")),
        ("crashed", run.get("crashed")),
    ]
    for label, value in rows:
        lines.append("| %s | `%s` |" % (label, value))
    lines.append("")
    if verdict_block.get("not_executed_reason"):
        lines.append("**Nothing was executed:** %s"
                     % verdict_block["not_executed_reason"])
        lines.append("")
    indicative = verdict_block.get("indicative") or []
    if indicative:
        lines.append("### Caveats (these qualify the level above)")
        lines.extend("* %s" % note for note in indicative)
        lines.append("")
    if env.get("shell_profile") == "server":
        lines.append("> GitHub-hosted Windows runners are **Windows Server**. A "
                     "taskbar, Start-menu or notification mod cannot be judged "
                     "here; only that it compiles, loads and does not crash.")
        lines.append("")
    lines.append("> The mod was loaded by this bench's shim host, not by the "
                 "Windhawk engine, so internal symbol hooks stay "
                 "`not_verifiable_here` even on real Windows.")
    screenshots = (rep.get("artifacts") or {}).get("screenshots") or []
    if screenshots:
        lines.append("")
        lines.append("Screenshot: `%s`" % screenshots[0])
    return "\n".join(lines) + "\n"


def selftest():
    """What this backend can do *here*, printed honestly. Exit 0 always."""
    backend = WindowsRunnerBackend()
    ok, reason = backend.available()
    env = backend.environment()
    print("backend: %s (%s)" % (backend.name, backend.label))
    print("available: %s - %s" % ("yes" if ok else "no", reason))
    print("os=%r edition=%r build=%r product_type=%r shell_profile=%r"
          % (env.get("os"), env.get("edition"), env.get("build_number"),
             env.get("product_type"), env.get("shell_profile")))
    print("vm_used=%r runner=%r image=%r" % (env.get("vm_used"), env.get("runner"),
                                             env.get("runner_image")))
    for note in env.get("notes") or []:
        print("note: %s" % note)
    notes = indicative_notes(env)
    print("indicative: %s" % ("; ".join(notes) if notes else "none"))
    return 0


def main(argv=None):
    """`python3 -m awt.backends.windows_ci [--mod FILE] [--json] [--selftest]`."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="python3 -m awt.backends.windows_ci",
        description="Run a mod through the native Windows backend (CI).")
    parser.add_argument("--mod", help="mod source (.wh.cpp) to build and run")
    parser.add_argument("--seconds", type=int, default=6,
                        help="how long the mod stays loaded (default 6)")
    parser.add_argument("--mode", default="auto",
                        help="host mode: auto|self|target (default auto)")
    parser.add_argument("--target", help="target process for --mode target")
    parser.add_argument("--screenshot", action="store_true",
                        help="capture the desktop after the run")
    parser.add_argument("--json", help="write the report JSON here")
    parser.add_argument("--selftest", action="store_true",
                        help="report what this backend can do here, and exit")
    parser.add_argument("--summary", metavar="REPORT.json",
                        help="print a Markdown summary of an existing report "
                             "(for a CI step summary) and exit")
    args = parser.parse_args(argv)

    if args.summary:
        with open(args.summary, encoding="utf-8") as f:
            print(summarise_markdown(json.load(f)), end="")
        return 0
    if args.selftest or not args.mod:
        return selftest()
    backend = WindowsRunnerBackend()
    rep = backend.run(args.mod, seconds=args.seconds, mode=args.mode,
                      target=args.target, screenshot=args.screenshot)
    text = json.dumps(rep, indent=2, sort_keys=True)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print("report written to %s" % args.json)
    print(text)
    return exit_code_for(rep)


if __name__ == "__main__":
    sys.exit(main())
