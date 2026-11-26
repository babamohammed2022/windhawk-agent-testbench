#!/usr/bin/env python3
"""Offline tests for the desktop lab orchestration.

Every image and log here is a generated fixture. These tests prove the Python
logic and cleanup discipline; they do NOT prove that desktop.cpp compiled or
that Wine captured a real scene. See tests/run_desktop_tests.py for that.
"""
from contextlib import ExitStack, nullcontext, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from awt import cli, desktop, png

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "visual-playground.wh.cpp"


def temporary():
    (ROOT / ".sandbox").mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=ROOT / ".sandbox")


def bmp(width=960, height=-600, accent=False, notice=False):
    """Top-down 32-bit BGRA fixture; accent/notice colors are test pixels."""
    w, h = width, abs(height)
    pixels = bytearray(b"\xf0\xf4\xf7\x00" * (w * h))

    def rect(x0, y0, x1, y1, rgb):
        for y in range(y0, min(y1, h)):
            for x in range(x0, min(x1, w)):
                i = (y * w + x) * 4
                pixels[i:i + 4] = bytes((rgb[2], rgb[1], rgb[0], 0))

    if accent:
        rect(16, 48, 560, 84, (24, 95, 160))
    if notice:
        rect(686, 390, 940, 542, (250, 220, 120))
    header = struct.pack("<2sIHHI", b"BM", 54 + len(pixels), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, w, height, 1, 32, 0, len(pixels), 0, 0, 0, 0)
    return header + info + bytes(pixels)


def test_validate_actions_rejects_unknown_commands_and_unsafe_ids():
    scenario = desktop.validate_actions(desktop.SCENARIOS["accent-notice"]["actions"])
    assert len(scenario) == 7 and scenario[0].startswith("create alpha")
    for bad in (["explorer.exe"], ["create ../../etc passwd"], ["click", "a b"],
                ["click !!!"], ["wait abc"], ["x" * 201]):
        try:
            desktop.validate_actions(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("accepted an unsafe action: %r" % bad)


def test_parse_output_and_assertions_on_generated_fixture():
    output = "\n".join([
        "[awt-host] init returned 1",
        "[awt-desktop] action create id=alpha class=AwtDesktopWindow title=Alpha window ok=1",
        "[awt-desktop] action create id=beta class=AwtDesktopWindow title=Beta window ok=1",
        "[awt-desktop] action click id=alpha target=notice before=0 after=1 ok=1",
        "[awt-desktop] action minimize id=beta minimized=1 ok=1",
        "[awt-desktop] action close id=beta closed=1 ok=1",
        "[awt-desktop] capture written",
    ])
    parsed = desktop.parse_desktop_output(output, "")
    assert [a["action"] for a in parsed["actions"]] == [
        "create", "create", "click", "minimize", "close"]
    assert parsed["captured"] and not parsed["script_failed"]
    assert parsed["host"]["init_returned"] is True
    image = png.Image(10, 10, "RGB")
    for x in range(10):
        for y in range(10):
            image.set(x, y, (24, 95, 160) if x < 5 else (250, 220, 120))
    assertions = [{**a, "value": 40} if a["kind"] == "pixels" else a
                  for a in desktop.scenario_assertions("accent-notice", True)]
    results = desktop.evaluate_assertions(assertions, parsed, image)
    assert all(r["passed"] for r in results)
    assert desktop.count_color(image, (24, 95, 160)) == 50
    # The negative control inverts only the accent expectation.
    control = desktop.scenario_assertions("accent-notice", False)
    accent = next(a for a in control if a["id"] == "accent_visible")
    assert accent["rule"] == "at_most" and accent["value"] == 100


def fake_desktop_exe(td):
    path = Path(td) / "desktop.exe"
    path.write_bytes(b"fixture-not-a-PE")
    return str(path)


def fake_build(mod_path, sandbox, settings_override=None):
    directory = Path(sandbox) / "build"
    directory.mkdir(parents=True, exist_ok=True)
    dll = directory / "mod.dll"
    dll.write_bytes(b"fixture-not-a-DLL")
    settings = directory / "settings.json"
    settings.write_text(json.dumps(settings_override or {"enabled": 1}))
    return {"ok": True, "errors": [], "warnings": [], "settings": settings_override or {"enabled": 1},
            "settings_override_unknown": [], "dll": str(dll), "settings_json": str(settings)}


def enter_ready(stack, root):
    """Enter the prerequisite patches into an ExitStack; no toolchain is used."""
    stack.enter_context(patch.object(desktop, "ROOT", Path(root)))
    stack.enter_context(patch.object(desktop.builder, "missing_toolchain", return_value=[]))
    stack.enter_context(patch.object(desktop.host_env, "find_wine", return_value="wine"))
    stack.enter_context(patch.object(desktop.preview, "find_wineserver", return_value="wineserver"))
    stack.enter_context(patch.object(desktop.shutil, "which", side_effect=lambda tool: tool))
    stack.enter_context(patch.object(desktop.subprocess, "run",
        return_value=SimpleNamespace(stdout="wine-test\n", returncode=0)))


def command_fixture(directory, code=0, timed_out=False, write_capture=True):
    def command(cmd, environment, log, limit):
        if "--capture" in cmd:
            Path(log).write_text(
                "[awt-host] init returned 1\n"
                "[awt-desktop] action create id=alpha title=Alpha ok=1\n"
                "[awt-desktop] action create id=beta title=Beta ok=1\n"
                "[awt-desktop] action click id=alpha target=notice before=0 after=1 ok=1\n"
                "[awt-desktop] action minimize id=beta minimized=1 ok=1\n"
                "[awt-desktop] action close id=beta closed=1 ok=1\n"
                "[awt-desktop] capture written\n")
            if write_capture:
                (Path(directory) / "capture.bmp").write_bytes(
                    bmp(accent=True, notice=True))
            assert environment["WINEPREFIX"] == str(Path(directory) / "prefix")
            return code, timed_out
        return 0, False
    return command


def test_missing_prerequisites_report_is_honest_and_builds_nothing():
    with temporary() as td, patch.object(desktop, "ROOT", Path(td)), \
         patch.object(desktop.builder, "missing_toolchain", return_value=["MinGW"]), \
         patch.object(desktop.host_env, "find_wine", return_value=None), \
         patch.object(desktop.shutil, "which", return_value=None), \
         patch.object(desktop.builder, "build") as build:
        result = desktop.generate(EXAMPLE)
        assert result["status"] == "not_executed"
        assert "MinGW" in result["reason"] and "Wine" in result["reason"]
        assert result["verification"]["verified"] == []
        assert result["artifacts"] == {}
        build.assert_not_called()
        report = Path(result["output"]) / "report.json"
        assert json.loads(report.read_text())["status"] == "not_executed"


def test_end_to_end_fake_run_passes_assertions_and_cleans_up():
    with temporary() as td, ExitStack() as stack, \
         patch.object(desktop.builder, "build", side_effect=fake_build), \
         patch.object(desktop.builder, "build_desktop", side_effect=lambda sandbox=None: fake_desktop_exe(td)), \
         patch.object(desktop.preview, "display_session", return_value=nullcontext(":345")), \
         patch.object(desktop.preview, "_command") as run:
        enter_ready(stack, td)
        def command(cmd, environment, log, limit):
            # The attempt directory is created inside generate(), so derive it
            # from the log path rather than passing it in advance.
            return command_fixture(Path(log).parent)(cmd, environment, log, limit)
        run.side_effect = command
        result = desktop.generate(EXAMPLE)
        assert result["status"] == "passed", result.get("reason")
        assert all(a["passed"] for a in result["assertions"])
        assert len(result["actions_observed"]) == 5
        assert Path(result["artifacts"]["screenshot"]).is_file()
        assert result["cleanup"]["performed"] and result["cleanup"]["ok"]
        assert any("mod init returned true" in item
                   for item in result["verification"]["verified"])
        assert result["process"]["explorer"] is False
        assert "real Explorer / Start menu / taskbar behaviour" in \
            result["verification"]["not_verifiable_here"]
        assert [c.args[0] for c in run.call_args_list[-2:]] == [
            ["wineserver", "-k"], ["wineserver", "-w"]]


def test_timeout_and_failed_cleanup_are_never_a_pass():
    with temporary() as td, ExitStack() as stack, \
         patch.object(desktop.builder, "build", side_effect=fake_build), \
         patch.object(desktop.builder, "build_desktop", side_effect=lambda sandbox=None: fake_desktop_exe(td)), \
         patch.object(desktop.preview, "display_session", return_value=nullcontext(":345")), \
         patch.object(desktop.preview, "_command") as run:
        enter_ready(stack, td)
        run.side_effect = lambda cmd, env, log, limit: (
            (None, True) if "--capture" in cmd else (0, False))
        result = desktop.generate(EXAMPLE)
        assert result["status"] == "run_failed"
        assert "timed_out=True" in result["reason"]
        assert "screenshot" not in result["artifacts"]
        assert result["cleanup"]["performed"]
    with temporary() as td, ExitStack() as stack, \
         patch.object(desktop.builder, "build", side_effect=fake_build), \
         patch.object(desktop.builder, "build_desktop", side_effect=lambda sandbox=None: fake_desktop_exe(td)), \
         patch.object(desktop.preview, "display_session", return_value=nullcontext(":345")), \
         patch.object(desktop.preview, "_command") as run:
        enter_ready(stack, td)
        run.side_effect = lambda cmd, env, log, limit: (
            (0, False) if "--capture" in cmd else (1, False))
        result = desktop.generate(EXAMPLE)
        assert result["status"] == "run_failed" and "cleanup" in result["reason"]
        assert result["cleanup"]["ok"] is False


def test_build_failure_never_starts_a_scene():
    def broken(*args, **kwargs):
        return {**fake_build(*args, **kwargs), "ok": False,
                "errors": [{"message": "compile failed"}]}
    with temporary() as td, ExitStack() as stack, \
         patch.object(desktop.builder, "build", side_effect=broken), \
         patch.object(desktop.preview, "display_session") as display:
        enter_ready(stack, td)
        result = desktop.generate(EXAMPLE)
        assert result["status"] == "build_failed"
        assert result["artifacts"] == {}
        display.assert_not_called()


def test_cli_dispatch_status_codes_and_usage():
    for status, expected in [("passed", 0), ("not_executed", 3), ("run_failed", 1)]:
        with patch.object(desktop, "generate",
                          return_value={"status": status, "reason": "fixture",
                                        "output": "/tmp/report", "artifacts": {}}), \
             redirect_stdout(io.StringIO()):
            assert cli.main(["desktop", str(EXAMPLE)]) == expected
    with patch.object(desktop, "generate", return_value={
            "status": "not_executed", "output": "/tmp/r", "artifacts": {}}) as generate, \
         redirect_stdout(io.StringIO()):
        desktop.main([str(EXAMPLE), "--no-mod-effect"])
        assert generate.call_args.kwargs["expect_mod_effect"] is False
    for args in ([str(EXAMPLE), "--no-mod"], [],
                 [str(EXAMPLE), "--settle-ms", "0"]):
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            try:
                code = desktop.main(args)
            except SystemExit as exc:
                code = exc.code
            assert code == 2, "usage error was accepted: %r" % args


def test_parse_output_records_probe_appbar_and_errors():
    output = "\n".join([
        "[awt-desktop] probe class=Progman title=Program Manager",
        "[awt-desktop] probe end shell_tray_conflict=0",
        "[awt-desktop] shell_class_emulation=1 class=Shell_TrayWnd",
        "[awt-desktop] appbar ABM_NEW returned=1",
        "[awt-desktop] error RegisterClassExW fallback err=1410",
        "[awt-desktop] unhandled-exception code=0xc0000005 address=0x1",
        "[awt-desktop] action create id=alpha title=Alpha ok=1",
    ])
    parsed = desktop.parse_desktop_output(output, "")
    assert parsed["shell_class_emulation"] == 1
    assert len(parsed["probe"]) == 1 and parsed["probe"][0]["class"] == "Progman"
    assert any("ABM_NEW" in line for line in parsed["appbar"])
    assert any("RegisterClassExW" in e for e in parsed["errors"])
    assertion = desktop.evaluate_assertions(
        [{"id": "no_crash", "kind": "no_crash"}], parsed, None)
    assert assertion[0]["passed"] is False


def test_taskbar_compat_scenario_is_not_an_accent_assertion():
    scenario = desktop.SCENARIOS["taskbar-compat"]
    assert scenario["profile"] == "win10"
    assert [a["id"] for a in scenario["assertions"]].count("accent_visible") == 0
    assert any(a["kind"] == "no_crash" for a in scenario["assertions"])
    assert desktop.validate_actions(scenario["actions"])


def test_profile_is_reported_and_passed_to_the_host():
    with temporary() as td, ExitStack() as stack, \
         patch.object(desktop.builder, "build", side_effect=fake_build), \
         patch.object(desktop.builder, "build_desktop",
                      side_effect=lambda sandbox=None: fake_desktop_exe(td)), \
         patch.object(desktop.preview, "display_session", return_value=nullcontext(":345")), \
         patch.object(desktop.preview, "_command") as run:
        enter_ready(stack, td)
        def command(cmd, environment, log, limit):
            if "--capture" in cmd:
                assert cmd[cmd.index("--profile") + 1] == "basic"
                Path(log).write_text(
                    "[awt-host] init returned 1\n"
                    "[awt-desktop] action create id=alpha title=Alpha ok=1\n"
                    "[awt-desktop] action create id=beta title=Beta ok=1\n"
                    "[awt-desktop] action click id=alpha target=notice before=0 after=1 ok=1\n"
                    "[awt-desktop] action minimize id=beta minimized=1 ok=1\n"
                    "[awt-desktop] action close id=beta closed=1 ok=1\n"
                    "[awt-desktop] capture written\n")
                (Path(log).parent / "capture.bmp").write_bytes(
                    bmp(accent=True, notice=True))
            return 0, False
        run.side_effect = command
        result = desktop.generate(EXAMPLE, profile="basic")
        assert result["profile"] == "basic"
        assert result["status"] == "passed", result.get("reason")
        assert result["shell_probe"] == [] and result["appbar"] == []


def test_describe_mod_source_is_bounded_and_heuristic():
    with temporary() as temp:
        source = Path(temp) / "sample.wh.cpp"
        source.write_text(
            "// SetFunctionHook mentioned only in a comment\n"
            "WindhawkUtils::HookSymbols(module, hooks, 1);\n"
            "#include <faked.dll>\n", encoding="utf-8")
        result = desktop.describe_mod_source(source)
    assert len(result["internal_hooks"]) <= 12
    assert len(result["internal_modules"]) <= 12
    assert "Not evidence" in result["note"]


def test_mod_analysis_is_written_when_prerequisites_are_absent():
    with temporary() as temp, ExitStack() as stack, \
         patch.object(desktop, "missing_prerequisites",
                      return_value=["Wine (absent in this sandbox)"]):
        source = Path(temp) / "sample.wh.cpp"
        source.write_text("WindhawkUtils::HookSymbols(module, hooks, 1);\n", encoding="utf-8")
        stack.enter_context(patch.object(desktop, "ROOT", Path(temp)))
        result = desktop.generate(source)
    assert result["status"] == "not_executed"
    assert result["mod_analysis"]["internal_hooks"]
    assert any("internal-symbols:" in x
               for x in result["verification"]["not_verifiable_here"])
    assert not result["artifacts"]
