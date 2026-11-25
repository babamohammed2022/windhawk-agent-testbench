"""Wine visual preview: isolated clean/mod runs of a real Win32 specimen.

No browser-exposed execution API, no fabricated screenshots, no shell emulation.
Artifacts are written under .sandbox/previews/<unique-id> for static viewing.
"""
import argparse
import base64
import hashlib
import html
import json
import os
from pathlib import Path
import select
import shutil
import signal
import struct
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager

from . import analyzer, builder, env as host_env, metadata, png, staticchecks, verdict, visualdiff

ROOT = Path(builder.REPO)
DISCLAIMER = ("Wine + Windhawk shim, not real Windows/Windhawk. This is a Win32 specimen, "
              "not the application's real target or Explorer. Pixel differences are observations, "
              "not functional assertions. Equal images may mean the mod had no visible effect.")


def find_wineserver(wine):
    """Debian wine64 may ship only /usr/lib/wine/wineserver64, not a PATH wrapper."""
    override = os.environ.get("AWT_WINESERVER")
    if override:
        return shutil.which(override)
    if wine:
        directory = Path(shutil.which(wine) or wine).resolve().parent
        for name in ("wineserver", "wineserver64"):
            candidate = directory / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    found = shutil.which("wineserver") or shutil.which("wineserver64")
    if found:
        return found
    if wine and str(directory) in ("/usr/bin", "/usr/lib/wine"):
        for private in ("/usr/lib/wine", "/usr/lib64/wine", "/usr/lib/x86_64-linux-gnu/wine",
                        "/opt/wine-stable/bin", "/opt/wine-devel/bin", "/opt/wine-staging/bin"):
            for name in ("wineserver64", "wineserver"):
                candidate = Path(private) / name
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
    return None


def windows_path(path):
    return "Z:" + str(Path(path).resolve()).replace("/", "\\")


def decode_capture(data):
    """Strict reader for the playground's 32-bit BI_RGB BMP, bounded before allocation.

    Accept top-down and bottom-up rows; ignore the undefined GDI alpha byte.
    This is intentionally not a general BMP decoder.
    """
    if len(data) < 54 or data[:2] != b"BM":
        raise ValueError("capture is not a complete BMP")
    size, offset = struct.unpack_from("<I4xI", data, 2)
    header, width, height, planes, bits, compression = struct.unpack_from("<IiiHHI", data, 14)
    if header != 40 or offset != 54 or planes != 1 or bits != 32 or compression != 0:
        raise ValueError("capture must be a 32-bit BI_RGB BITMAPINFOHEADER BMP")
    if not 0 < width <= 2048 or not 0 < abs(height) <= 1536:
        raise ValueError("capture dimensions outside supported bounds")
    expected = offset + width * abs(height) * 4
    if size != expected or len(data) != expected:
        raise ValueError("capture length does not match its dimensions")
    pixels = bytearray(width * abs(height) * 3)
    for y in range(abs(height)):
        source_y = y if height < 0 else abs(height) - 1 - y
        row = data[offset + source_y * width * 4:offset + (source_y + 1) * width * 4]
        target = y * width * 3
        pixels[target:target + width * 3:3] = row[2::4]
        pixels[target + 1:target + width * 3:3] = row[1::4]
        pixels[target + 2:target + width * 3:3] = row[0::4]
    return png.Image(width, abs(height), "RGB", pixels)


def _stop(proc):
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=5)


def _command(command, environment, log, timeout):
    with open(log, "ab") as output:
        proc = subprocess.Popen(command, env=environment, stdout=output,
                                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = proc.wait(timeout=timeout)
            return code, False
        except subprocess.TimeoutExpired:
            return None, True
        finally:
            _stop(proc)


@contextmanager
def display_session(log):
    """Xvfb chooses a free display atomically; never reuse another user's X server."""
    read_fd, write_fd = os.pipe()
    proc = None
    try:
        with open(log, "ab") as output:
            proc = subprocess.Popen(["Xvfb", "-displayfd", str(write_fd), "-screen", "0",
                                     "1024x768x24", "-nolisten", "tcp"],
                                    stdout=output, stderr=subprocess.STDOUT,
                                    pass_fds=(write_fd,), start_new_session=True)
        os.close(write_fd)
        write_fd = None
        if not select.select([read_fd], [], [], 10)[0]:
            raise RuntimeError("Xvfb startup timed out")
        number = os.read(read_fd, 64).decode("ascii").strip()
        if not number.isdecimal() or proc.poll() is not None:
            raise RuntimeError("Xvfb did not provide a ready display")
        yield ":" + number
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
        if proc is not None:
            _stop(proc)


def run_phase(directory, executable, build, with_mod, wine, wineserver, settle_ms):
    """Separate prefix, display, process, storage and logs for each phase."""
    directory = Path(directory)
    directory.mkdir()
    log = directory / "host.log"
    environment = {k: v for k, v in os.environ.items()
                   if not k.startswith(("AWT_", "WINE"))}
    prefix = directory / "prefix"
    environment.update(WINEPREFIX=str(prefix), WINEARCH="win64", WINEDEBUG="-all",
                       WINEDLLOVERRIDES="winemenubuilder.exe=d")
    result = {"with_mod": with_mod, "status": "run_failed", "log": str(log),
              "capture": None, "exit_code": None, "timed_out": False}
    with display_session(directory / "xvfb.log") as display:
        environment["DISPLAY"] = display
        try:
            # First invocation initializes a NEW prefix and disables the debugger there.
            code, timed_out = _command(
                [wine, "reg", "add", r"HKCU\Software\Wine\Debug", "/v", "CrashHandler",
                 "/d", "", "/f"], environment, directory / "prefix.log", 90)
            if timed_out or code != 0:
                result.update(reason="Wine prefix initialization failed", exit_code=code,
                              timed_out=timed_out)
                return result
            for name in ("mod.log", "stubs.log"):
                (directory / name).touch()
            (directory / "storage").mkdir()
            environment.update(
                AWT_MOD_LOG=windows_path(directory / "mod.log"),
                AWT_STUB_LOG=windows_path(directory / "stubs.log"),
                AWT_SETTINGS_JSON=windows_path(build["settings_json"]),
                AWT_STORAGE_DIR=windows_path(directory / "storage") + "\\")
            command = [wine, windows_path(executable), "--capture",
                       windows_path(directory / "capture.bmp"), "--settle-ms", str(settle_ms)]
            if with_mod:
                command.extend(["--mod", windows_path(build["dll"])])
            code, timed_out = _command(command, environment, log, 30)
            output = log.read_text(encoding="utf-8", errors="replace")
            mod_log = (directory / "mod.log").read_text(encoding="utf-8", errors="replace")
            markers = verdict.parse_host_markers(output, mod_log)
            result.update(exit_code=code, timed_out=timed_out, markers=markers,
                          stubs=(directory / "stubs.log").read_text().split())
            valid = (code == 0 and not timed_out and not markers["crashed"]
                     and "[awt-preview] capture written" in output
                     and (not with_mod or markers["init_returned"] is True))
            if not valid:
                result["reason"] = "capture requires clean completion, capture marker and (with mod) successful init"
                return result
            bmp = directory / "capture.bmp"
            # Check size before reading untrusted mod-influenced output.
            if not bmp.is_file() or bmp.stat().st_size > 54 + 2048 * 1536 * 4:
                raise ValueError("capture is missing or exceeds the size limit")
            image = decode_capture(bmp.read_bytes())
            png.write(directory / "capture.png", image)
            result.update(status="captured", capture=str(directory / "capture.png"),
                          width=image.width, height=image.height)
            return result
        finally:
            # Only this phase's fresh prefix: never global pkill or shared Wine state.
            code, timed_out = _command([wineserver, "-k"], environment,
                                      directory / "cleanup.log", 10)
            if not timed_out and code == 0:
                code, timed_out = _command([wineserver, "-w"], environment,
                                          directory / "cleanup.log", 10)
            result["cleanup_ok"] = code == 0 and not timed_out
            if not result["cleanup_ok"]:
                result.update(status="run_failed", reason="Wine prefix cleanup did not complete; prefix retained")
            elif prefix.is_dir():
                # Fresh prefixes are large and never needed by the static viewer.
                shutil.rmtree(prefix)


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _image_data(path):
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")


def render_html(result):
    """Self-contained, escaped viewer. No execution endpoints or external assets."""
    esc = lambda value: html.escape(str(value), quote=True)
    panels = []
    for name, title in (("before", "Without mod"), ("after", "With mod")):
        phase = result.get("phases", {}).get(name) or {}
        image = (f'<img alt="{title}: captured Win32 client area" src="{_image_data(phase["capture"])}">'
                 if phase.get("status") == "captured" else
                 '<p class="empty">No validated capture. Nothing is shown as a successful preview.</p>')
        panels.append(f'<section><h2>{title}</h2>{image}<p>{esc(phase.get("reason", phase.get("status", "not executed")))}</p></section>')
    diff = result.get("diff_map")
    if diff:
        panels.append(f'<section><h2>Pixel difference (derived)</h2><img alt="Derived difference heatmap" src="{_image_data(diff)}"></section>')
    diagnostics = []
    for name, phase in result.get("phases", {}).items():
        log = Path(phase["log"])
        if log.is_file():
            with log.open(encoding="utf-8", errors="replace") as stream:
                text = stream.read(24000)
        else:
            text = "No host log"
        diagnostics.append(f'<details><summary>{esc(name)} host log (first 24k characters)</summary><pre>{esc(text)}</pre></details>')
    summary = dict(result)
    return '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Windhawk visual playground</title><style>
:root{color-scheme:dark;font:16px system-ui;background:#101722;color:#e8eef8}
body{max-width:1440px;margin:auto;padding:32px}h1{font-size:32px;margin-bottom:8px}
.label{color:#95b9ef;text-transform:uppercase;letter-spacing:.1em;font-size:13px}
.notice{border-left:4px solid #e5b462;padding:16px;background:#242536;line-height:1.6}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr));gap:20px;margin:28px 0}
section,details{background:#1a2433;border:1px solid #35455b;border-radius:12px;padding:20px;margin-bottom:16px}
section img{width:100%;height:auto;background:#fff}h2{font-size:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}
.empty{padding:50px 12px;color:#bbc8db}summary{cursor:pointer}a{color:#aacdff}</style><body>
<p class="label">Wine / shim · static before-after preview</p><h1>Windhawk visual playground</h1>''' + (
        f'<p>Mod: <strong>{esc(result.get("mod", "unknown"))}</strong> · Status: <strong>{esc(result["status"])}</strong></p>'
        f'<p class="notice">{esc(DISCLAIMER)}<br>{esc(result.get("reason", ""))}</p>'
        '<p>Rerun the CLI after editing the mod, then reopen this new report. No hot reload, live desktop or browser-triggered execution.</p>'
        '<div class="grid">' + "".join(panels) + '</div>' + "".join(diagnostics) +
        '<details><summary>Manifest / provenance</summary><pre>' + esc(json.dumps(summary, indent=2, ensure_ascii=False)) +
        '</pre></details></body></html>')


def generate(mod_path, settings_override=None, settle_ms=800):
    """Always produce a manifest/viewer for expected unavailable/build/run outcomes."""
    if not 100 <= settle_ms <= 10000:
        raise ValueError("settle-ms must be between 100 and 10000")
    source = Path(mod_path).read_text(encoding="utf-8")
    meta = metadata.parse_metadata(source)
    # The shared builder uses @id in artifact paths. Keep preview artifacts in
    # their caller-owned directory even for malformed metadata.
    mod_id = meta["raw"].get("id", "")
    if mod_id in (".", "..") or not mod_id or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for c in mod_id):
        raise ValueError("preview requires a non-empty @id using letters, digits, dot, underscore or hyphen")
    settings_override = builder.validate_settings_override(settings_override)
    output = ROOT / ".sandbox" / "previews" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:10])
    output.mkdir(parents=True)
    result = {"preview_schema": 1, "mod": str(Path(mod_path).resolve()), "status": "not_executed",
              "backend": "wine", "shell_profile": "wine", "output": str(output),
              "disclaimer": DISCLAIMER, "phases": {}, "settle_ms": settle_ms,
              "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
              "target_mapping": {"requested_includes": meta["include"],
                                 "actual_target": "awt-playground.exe",
                                 "mode": "forced specimen; metadata include/exclude filters not applied"},
              "static_checks": staticchecks.run_checks(source),
              "verification": {"verified": [], "not_verifiable_here": [
                  "real target integration", "Windows shell behaviour", "real Windhawk lifecycle",
                  "functional correctness"], "requires_real_windows": True}}
    try:
        absent = builder.missing_toolchain()
        wine = host_env.find_wine()
        wineserver = find_wineserver(wine)
        if sys.platform != "linux":
            absent.append("preview currently requires Linux")
        if not wine:
            absent.append("Wine")
        if not wineserver or not shutil.which(wineserver):
            absent.append("wineserver (or AWT_WINESERVER)")
        if not shutil.which("Xvfb"):
            absent.append("Xvfb")
        if absent:
            result["reason"] = "missing prerequisites: " + ", ".join(absent) + "; no captures produced"
            return result
        version = subprocess.run([wine, "--version"], capture_output=True, text=True, timeout=10)
        result["wine_version"] = version.stdout.strip()
        build = builder.build(mod_path, sandbox=str(output), settings_override=settings_override)
        result["build"] = {k: build[k] for k in ("ok", "errors", "warnings", "settings", "settings_override_unknown")}
        if not build["ok"]:
            result.update(status="build_failed", reason="mod compilation failed; no screenshots substituted")
            return result
        result["verification"]["verified"].append("mod build")
        executable = builder.build_playground(sandbox=str(output))
        result["binaries"] = {"playground_sha256": _hash(executable), "mod_sha256": _hash(build["dll"])}
        result["analysis"] = analyzer.analyze(source)
        for name, with_mod in (("before", False), ("after", True)):
            phase = run_phase(output / name, executable, build, with_mod, wine, wineserver, settle_ms)
            result["phases"][name] = phase
            if phase["status"] != "captured":
                result.update(status="run_failed", reason=name + ": " + phase.get("reason", "capture failed"))
                return result
            result["verification"]["verified"].append(name + " specimen client pixels captured")
        result["verification"]["verified"].append("mod init returned true in specimen")
        diff = output / "difference.png"
        comparison = visualdiff.compare(result["phases"]["after"]["capture"],
                                        [result["phases"]["before"]["capture"]],
                                        diff_map=str(diff))
        result["visual_comparison"] = comparison
        if comparison["overall"]["status"] not in ("match", "mismatch"):
            result.update(status="comparison_unavailable", reason="captures exist but are not comparable; see visual_comparison")
        else:
            result.update(status="captured", reason="two real captures; pixel match/mismatch is descriptive, not pass/fail")
        if diff.is_file():
            result["diff_map"] = str(diff)
        return result
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        result.update(status="error", reason="%s: %s" % (type(exc).__name__, exc))
        return result
    finally:
        (output / "preview.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        (output / "index.html").write_text(render_html(result), encoding="utf-8")


def main(argv=None):
    from .cli import _load_settings_override
    parser = argparse.ArgumentParser(prog="awt preview")
    parser.add_argument("mod")
    parser.add_argument("--settle-ms", type=int, default=800)
    parser.add_argument("--settings-json")
    parser.add_argument("--settings", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        result = generate(args.mod, _load_settings_override(args.settings_json, args.settings), args.settle_ms)
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    print("preview: %s — %s" % (result["status"], result.get("reason", "")))
    print("viewer: %s/index.html" % result["output"])
    print("manifest: %s/preview.json" % result["output"])
    return 0 if result["status"] == "captured" else 3 if result["status"] == "not_executed" else 1


if __name__ == "__main__":
    sys.exit(main())
