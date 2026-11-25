"""Offline preview orchestration tests; generated pixels here are test fixtures,
not evidence that the C++ specimen compiled or that a Wine capture succeeded.
"""
from contextlib import contextmanager, nullcontext, redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from awt import cli, png, preview

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples/visual-playground.wh.cpp"


def temporary():
    (ROOT / ".sandbox").mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=ROOT / ".sandbox")


def bmp(width=2, height=-1, pixels=b"\x00\x00\xff\x00\x00\xff\x00\xff"):
    return (struct.pack("<2sIHHI", b"BM", 54 + len(pixels), 0, 0, 54) +
            struct.pack("<IiiHHIIiiII", 40, width, height, 1, 32, 0, len(pixels), 0, 0, 0, 0) + pixels)


def test_capture_conversion_bgra_and_undefined_alpha():
    image = preview.decode_capture(bmp())
    assert (image.width, image.height, image.mode) == (2, 1, "RGB")
    assert image.get(0, 0) == (255, 0, 0)
    assert image.get(1, 0) == (0, 255, 0)
    assert png.from_bytes(png.to_bytes(image)).pixels == image.pixels


def test_capture_conversion_bottom_up_and_top_down():
    raw = b"\x00\x00\xff\x00\xff\x00\x00\x00"
    assert preview.decode_capture(bmp(1, 2, raw)).get(0, 0) == (0, 0, 255)
    assert preview.decode_capture(bmp(1, -2, raw)).get(0, 0) == (255, 0, 0)


def test_capture_decoder_refuses_corrupt_unsupported_or_oversize_inputs():
    bad = [b"", b"BM", bmp()[:-1], bmp() + b"extra", bmp(3000, -1), bmp(2, 0)]
    for offset, fmt, value in [(0, "2s", b"no"), (10, "I", 55), (14, "I", 108),
                               (26, "H", 2), (28, "H", 24), (30, "I", 3), (2, "I", 0)]:
        data = bytearray(bmp())
        struct.pack_into("<" + fmt, data, offset, value)
        bad.append(data)
    for data in bad:
        try:
            preview.decode_capture(data)
        except ValueError:
            pass
        else:
            raise AssertionError("accepted malformed BMP")


def test_html_escapes_mod_logs_reason_and_manifest():
    with temporary() as td:
        log = Path(td) / "log.txt"
        log.write_text('<script>alert("mod log")</script>')
        result = {"mod": '<img src=x onerror="alert(1)">', "status": "error",
                  "reason": "<script>evil</script>", "phases": {"after": {"log": str(log)}}}
        html = preview.render_html(result)
        assert "<script>" not in html and "<img src=x" not in html
        assert "&lt;script&gt;" in html
        assert "Content-Security-Policy" in html and "default-src 'none'" in html
        assert "No validated capture" in html and "not functional assertions" in html


def test_html_embeds_only_successful_captures():
    with temporary() as td:
        image = Path(td) / "capture.png"
        log = Path(td) / "host.log"
        png.write(image, png.solid(2, 2, (12, 34, 56), "RGB"))
        result = {"status": "run_failed", "phases": {"before": {
            "status": "captured", "capture": str(image), "log": str(log)}, "after": {
            "status": "run_failed", "capture": str(image), "log": str(log)}}}
        html = preview.render_html(result)
        assert html.count('src="data:image/png;base64,') == 1
        assert "No validated capture" in html


@contextmanager
def ready(root):
    with patch.object(preview, "ROOT", Path(root)), \
         patch.object(preview.builder, "missing_toolchain", return_value=[]), \
         patch.object(preview.host_env, "find_wine", return_value="wine"), \
         patch.object(preview.shutil, "which", side_effect=lambda tool: tool), \
         patch.object(preview.subprocess, "run", return_value=SimpleNamespace(stdout="wine-test\n", returncode=0)):
        yield


def fake_build(mod_path, sandbox, settings_override=None):
    directory = Path(sandbox) / "build"
    directory.mkdir()
    dll = directory / "mod.dll"
    dll.write_bytes(b"test-not-a-real-DLL")
    settings = directory / "settings.json"
    settings.write_text(json.dumps(settings_override or {"enabled": 1}))
    return {"ok": True, "errors": [], "warnings": [], "settings": settings_override or {"enabled": 1},
            "settings_override_unknown": [], "dll": str(dll), "settings_json": str(settings)}


def fake_exe(sandbox):
    path = Path(sandbox) / "build/playground.exe"
    path.write_bytes(b"test-not-a-real-PE")
    return str(path)


def fake_phase(directory, executable, build, with_mod, wine, wineserver, settle_ms):
    directory = Path(directory)
    directory.mkdir()
    log = directory / "host.log"
    log.write_text("unit-test fixture log")
    image = directory / "capture.png"
    png.write(image, png.solid(16, 16, (10 if with_mod else 255, 50, 100), "RGB"))
    return {"status": "captured", "capture": str(image), "log": str(log), "with_mod": with_mod}


def test_unavailable_creates_honest_viewer_and_never_builds():
    with temporary() as td, patch.object(preview, "ROOT", Path(td)), \
         patch.object(preview.builder, "missing_toolchain", return_value=["MinGW"]), \
         patch.object(preview.host_env, "find_wine", return_value=None), \
         patch.object(preview.shutil, "which", return_value=None), \
         patch.object(preview.builder, "build") as build:
        result = preview.generate(EXAMPLE)
        assert result["status"] == "not_executed"
        assert result["phases"] == {} and result["verification"]["verified"] == []
        build.assert_not_called()
        output = Path(result["output"])
        assert json.loads((output / "preview.json").read_text())["status"] == "not_executed"
        assert "No validated capture" in (output / "index.html").read_text()


def test_generate_orders_before_after_and_keeps_unique_artifacts_and_provenance():
    with temporary() as td, ready(td), \
         patch.object(preview.builder, "build", side_effect=fake_build) as build, \
         patch.object(preview.builder, "build_playground", side_effect=fake_exe), \
         patch.object(preview, "run_phase", side_effect=fake_phase) as phase:
        result = preview.generate(EXAMPLE, {"enabled": 0})
        assert result["status"] == "captured"
        assert [call.args[3] for call in phase.call_args_list] == [False, True]
        assert result["visual_comparison"]["overall"]["status"] == "mismatch"
        assert len(result["binaries"]["mod_sha256"]) == 64
        assert result["verification"]["requires_real_windows"]
        assert build.call_args.kwargs["settings_override"] == {"enabled": 0}
        assert Path(result["diff_map"]).is_file()
        assert "data:image/png;base64," in (Path(result["output"]) / "index.html").read_text()
        second = preview.generate(EXAMPLE)
        assert second["output"] != result["output"]


def test_build_failure_and_phase_failure_never_substitute_a_capture():
    with temporary() as td, ready(td), patch.object(preview.builder, "build", side_effect=lambda *a, **k: {
        **fake_build(*a, **k), "ok": False, "errors": [{"message": "compile failed"}]}), \
         patch.object(preview, "run_phase") as phase:
        result = preview.generate(EXAMPLE)
        assert result["status"] == "build_failed"
        assert result["phases"] == {}
        phase.assert_not_called()
    with temporary() as td, ready(td), \
         patch.object(preview.builder, "build", side_effect=fake_build), \
         patch.object(preview.builder, "build_playground", side_effect=fake_exe), \
         patch.object(preview, "run_phase", return_value={"status": "run_failed", "reason": "timed out", "log": "missing"}) as phase:
        result = preview.generate(EXAMPLE)
        assert result["status"] == "run_failed"
        assert list(result["phases"]) == ["before"]
        assert "visual_comparison" not in result
        assert phase.call_count == 1


def test_runtime_error_still_produces_manifest():
    with temporary() as td, ready(td), \
         patch.object(preview.builder, "build", side_effect=fake_build), \
         patch.object(preview.builder, "build_playground", side_effect=RuntimeError("C++ compiler failed")):
        result = preview.generate(EXAMPLE)
        assert result["status"] == "error" and "compiler failed" in result["reason"]
        assert (Path(result["output"]) / "preview.json").is_file()


def test_cli_dispatch_settings_status_codes_and_invalid_arguments():
    for status, expected in [("captured", 0), ("not_executed", 3), ("build_failed", 1), ("run_failed", 1)]:
        with patch.object(preview, "generate", return_value={"status": status, "output": "/report"}) as generate, redirect_stdout(io.StringIO()):
            assert cli.main(["preview", str(EXAMPLE), "--settings", "enabled=false", "--settle-ms", "500"]) == expected
            assert generate.call_args.args[1:] == ({"enabled": 0}, 500)
    with redirect_stderr(io.StringIO()):
        assert preview.main([str(EXAMPLE), "--settle-ms", "0"]) == 2
        assert preview.main([str(EXAMPLE), "--settings", "invalid"]) == 2
        assert preview.main(["does-not-exist.wh.cpp"]) == 2


def test_phase_only_accepts_clean_completion_successful_init_and_valid_capture():
    for with_mod, output, code, timeout, valid in [
        (False, "[awt-preview] capture written", 0, False, True),
        (True, "[awt-host] init returned 1\n[awt-preview] capture written", 0, False, True),
        (True, "[awt-host] init returned 0\n[awt-preview] capture written", 0, False, False),
        (True, "[awt-preview] capture written", 0, False, False),
        (False, "[awt-preview] capture written", 1, False, False),
        (False, "[awt-preview] capture written", None, True, False),
        (False, "no marker", 0, False, False),
        (False, "unhandled exception\n[awt-preview] capture written", 0, False, False),
    ]:
        with temporary() as td:
            phase_dir = Path(td) / "before"
            def command(cmd, environment, log, limit):
                if "--capture" in cmd:
                    Path(log).write_text(output)
                    (phase_dir / "capture.bmp").write_bytes(bmp())
                    assert environment["WINEPREFIX"] == str(phase_dir / "prefix")
                    assert environment.get("AWT_AUTOINIT") is None
                    return code, timeout
                return 0, False
            with patch.object(preview, "display_session", return_value=nullcontext(":345")), \
                 patch.object(preview, "_command", side_effect=command) as run, \
                 patch.dict(os.environ, {"AWT_AUTOINIT": "1", "WINEPREFIX": "/do-not-touch"}):
                result = preview.run_phase(phase_dir, "playground.exe", {"dll": "mod.dll", "settings_json": "settings.json"},
                                           with_mod, "wine", "wineserver", 100)
            assert (result["status"] == "captured") == valid
            assert result["cleanup_ok"]
            assert run.call_args_list[-2].args[0] == ["wineserver", "-k"]
            assert run.call_args_list[-1].args[0] == ["wineserver", "-w"]


def test_phase_cleans_up_when_capture_decode_raises_and_on_prefix_failure():
    with temporary() as td:
        directory = Path(td) / "phase"
        def command(cmd, environment, log, limit):
            if "--capture" in cmd:
                Path(log).write_text("[awt-preview] capture written")
                (directory / "capture.bmp").write_bytes(b"invalid")
            return 0, False
        with patch.object(preview, "display_session", return_value=nullcontext(":345")), \
             patch.object(preview, "_command", side_effect=command) as run:
            try:
                preview.run_phase(directory, "exe", {"settings_json": "settings"}, False, "wine", "wineserver", 100)
            except ValueError:
                pass
            else:
                raise AssertionError("invalid capture accepted")
            assert run.call_args_list[-1].args[0] == ["wineserver", "-w"]
    with temporary() as td, patch.object(preview, "display_session", return_value=nullcontext(":345")), \
         patch.object(preview, "_command", side_effect=[(1, False), (0, False), (0, False)]) as run:
        result = preview.run_phase(Path(td) / "phase", "exe", {}, False, "wine", "wineserver", 100)
        assert result["status"] == "run_failed" and "initialization" in result["reason"]
        assert run.call_count == 3


def test_command_timeout_terminates_only_its_process_group():
    with temporary() as td, patch.object(preview.subprocess, "Popen") as popen, \
         patch.object(preview, "_stop") as stop:
        proc = popen.return_value
        proc.wait.side_effect = subprocess.TimeoutExpired("fixture", 1)
        assert preview._command(["fake"], {}, Path(td) / "log", 1) == (None, True)
        assert popen.call_args.kwargs["start_new_session"]
        stop.assert_called_once_with(proc)


def test_display_session_stops_xvfb_on_body_exception():
    with temporary() as td, patch.object(preview.subprocess, "Popen") as popen, \
         patch.object(preview.select, "select", return_value=([1], [], [])), \
         patch.object(preview.os, "read", return_value=b"333\n"), \
         patch.object(preview, "_stop") as stop:
        popen.return_value.poll.return_value = None
        try:
            with preview.display_session(Path(td) / "log") as display:
                assert display == ":333"
                raise RuntimeError("intentional body failure")
        except RuntimeError:
            pass
        stop.assert_called_once_with(popen.return_value)
        assert "-nolisten" in popen.call_args.args[0]


def test_display_startup_failure_is_cleaned_up():
    with temporary() as td, patch.object(preview.subprocess, "Popen") as popen, \
         patch.object(preview.select, "select", return_value=([], [], [])), \
         patch.object(preview, "_stop") as stop:
        try:
            with preview.display_session(Path(td) / "log"):
                raise AssertionError("must not yield an unready display")
        except RuntimeError as exc:
            assert "startup" in str(exc)
        stop.assert_called_once_with(popen.return_value)
