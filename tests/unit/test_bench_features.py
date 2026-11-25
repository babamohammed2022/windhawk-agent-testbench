"""Offline regression tests for diagnostics, overrides and batch orchestration.

These tests do NOT claim the C++ host compiled or that a Wine hook fired.
Runtime checks live separately in tests/run_tests.sh.
"""
import contextlib
import io
import json
from pathlib import Path
import re
import tempfile
from unittest.mock import patch
import xml.etree.ElementTree as ET

from awt import batch, builder, cli, coverage, crash, metadata, staticchecks, verdict

REPO = Path(__file__).resolve().parents[2]
SOURCE = (REPO / "examples/settings-demo.wh.cpp").read_text()


def tempdir():
    (REPO / ".sandbox").mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=REPO / ".sandbox")


def fake_report(level="loads_no_crash", **run):
    return {"mod": {"id": "example"}, "verdict": {"level": level},
            "run": {"init_returned": True, "exit_code": 0, **run}}


def test_activity_catalog_matches_host_source():
    source = (REPO / "host/host.cpp").read_text()
    markers = set(re.findall(r'Mark\("activity (\w+)"\)', source))
    assert markers == set(coverage.DRIVER_CATALOG)
    for name in markers:
        assert re.search(r"\b" + re.escape(name) + r"\s*\(", source), name


def test_activity_parser_deduplicates_only_host_markers():
    assert coverage.parse_driven_apis("[mod] activity Fake\n[awt-host] activity ReadFile\n[awt-host] activity ReadFile") == ["ReadFile"]
    assert coverage.parse_driven_apis(None) == []


def test_coverage_distinguishes_missing_stimulus_from_missing_log():
    ana = {"public_hooks": ["ReadFile", "WriteFile", "CreateFileW", "Other", "ReadFile"]}
    result = coverage.hook_coverage(ana, {"ReadFile": 2}, ["ReadFile", "WriteFile"])
    entries = {e["api"]: e for e in result["entries"]}
    assert entries["ReadFile"]["status"] == "fired"
    assert entries["WriteFile"]["status"] == "driven_no_log"
    assert entries["CreateFileW"]["status"] == "not_driven"
    assert entries["Other"]["wine_expectation"] == "unknown"
    assert result["summary"]["hooks"] == 4
    assert sum(result["summary"][k] for k in ("fired", "driven_no_log", "not_driven", "init_not_confirmed")) == 4


def test_coverage_unknown_drivers_and_unconfirmed_init_are_not_verified():
    ana = {"public_hooks": ["ReadFile"]}
    result = coverage.hook_coverage(ana, {}, None)
    assert result["entries"][0]["driven"] is None
    assert result["summary"]["driven_unknown"]
    result = coverage.hook_coverage(ana, {"ReadFile": 1}, ["ReadFile"], init_ok=False)
    assert result["summary"]["fired"] == 0
    assert result["entries"][0]["status"] == "init_not_confirmed"


def test_lifecycle_markers_are_observations_not_declarations():
    markers = verdict.parse_host_markers("\n".join([
        "[awt-host] init returned 1",
        "[awt-host] lifecycle Wh_ModAfterInit: called (after init)",
        "[awt-host] lifecycle Wh_ModSettingsChanged: not exported (optional)",
        "[awt-host] settings changed (reload=0)",
        "[awt-host] activity ReadFile",
    ]))
    assert markers["lifecycle"] == {"afterinit_called": True, "settings_changed_called": False,
                                    "settings_reload_called": True, "settings_reload_result": 0}
    assert markers["driven_apis"] == ["ReadFile"]


def test_diagnostics_do_not_change_verdict_ladder():
    markers = verdict.parse_host_markers("[awt-host] init returned 1\n[awt-host] activity ReadFile")
    for output in (None, "[awt-host] activity ReadFile"):
        rep = {}
        verdict.apply_run_result(rep, {"public_hooks": ["ReadFile"]}, markers, {},
                                 host_output=output, timed_out=True)
        assert rep["verdict"]["level"] == "loads_no_crash"
        assert rep["crash"]["kind"] == "timeout"
        assert rep["hook_coverage"]["summary"]["driven_unknown"] == (output is None)


def test_old_host_no_telemetry_is_unknown():
    markers = verdict.parse_host_markers("[awt-host] init returned 1")
    rep = {}
    verdict.apply_run_result(rep, {"public_hooks": ["ReadFile"]}, markers, {}, host_output="old host")
    assert rep["hook_coverage"]["summary"]["driven_unknown"]


def test_crash_attribution_is_never_guessed_from_a_frame():
    for output in ("wine: unhandled exception in ntdll", "Unhandled exception in shim.cpp", "Unhandled exception in mod.wh.cpp"):
        result = crash.classify_crash(output)
        assert result["kind"] == "crash"
        assert result["faulting_module"] == "unknown"


def test_crash_exit_codes_and_timeout_are_conservative():
    for code in (0xC0000005, -1073741819):
        assert crash.classify_crash("", exit_code=code)["crashed"]
        assert crash.classify_crash("", target_exit_code=code)["crashed"]
    for code in (0, 1, -9, -11, None):
        assert not crash.classify_crash("", exit_code=code)["crashed"]
    assert crash.classify_crash("", timed_out=True)["kind"] == "timeout"
    assert crash.classify_crash("", load_failed=True)["kind"] == "load_failure"
    assert crash.classify_crash("unhandled exception", timed_out=True)["kind"] == "crash"


def test_crash_evidence_is_bounded():
    result = crash.classify_crash(("Unhandled exception " + "x" * 500 + "\n") * 20)
    assert len(result["evidence"]) == 8
    assert all(len(line) <= 200 for line in result["evidence"])


def test_static_checks_settings_and_comment_handling():
    result = staticchecks.run_checks(SOURCE)
    assert result["summary"]["errors"] == 0
    assert not [c for c in result["checks"] if c["id"] == "settings.undeclared"]
    changed = SOURCE.replace('Wh_GetIntSetting(L"enabled")', 'Wh_GetIntSetting(L"enabld")')
    assert any(c["id"] == "settings.undeclared" and "enabld" in c["message"]
               for c in staticchecks.run_checks(changed)["checks"])
    result = staticchecks.run_checks('// BOOL Wh_ModInit() {}\nconst char* s="BOOL Wh_ModInit()";')
    assert any(c["id"] == "lifecycle.modinit" for c in result["checks"])


def test_static_no_include_and_multi_arch_are_diagnostic():
    source = SOURCE.replace('// @include         testapp.exe\n', '').replace('x86-64', 'x86 x86-64')
    ids = {c["id"] for c in staticchecks.run_checks(source)["checks"]}
    assert "metadata.include" in ids
    assert "architecture.partial" in ids
    assert "static" in staticchecks.run_checks(source)["note"]


def test_check_cli_output_and_missing_file():
    with tempdir() as td, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        output = Path(td) / "check.json"
        assert cli.main(["check", str(REPO / "examples/settings-demo.wh.cpp"), "--json", str(output)]) == 0
        assert json.loads(output.read_text())["summary"]["errors"] == 0
        assert cli.main(["check", str(Path(td) / "missing.wh.cpp")]) == 2


def test_merge_settings_preserves_defaults_reports_unknown_and_normalises_bools():
    defaults = {"enabled": 1, "label": "default"}
    effective, unknown = builder.merge_settings(defaults, {"enabled": False, "typo": 3})
    assert effective == {"enabled": 0, "label": "default", "typo": 3}
    assert type(effective["enabled"]) is int
    assert unknown == ["typo"]
    assert defaults == {"enabled": 1, "label": "default"}


def test_override_json_and_cli_precedence_and_utf8():
    with tempdir() as td:
        path = Path(td) / "settings.json"
        path.write_text('{"enabled":true,"retries":2,"greeting":"ciao"}')
        settings = cli._load_settings_override(str(path), ["enabled=false", "retries=-3", "greeting=caffè"])
        assert settings == {"enabled": 0, "retries": -3, "greeting": "caffè"}
        assert "caffè" in metadata.settings_json(settings)


def test_settings_reject_unsupported_shapes_escapes_and_ranges():
    invalid = [[], 1, {"": 2}, {"x": []}, {"x": {}}, {"x": None}, {"x": 0.5},
               {"x": 2**31}, {"x": "a\\b"}, {"x": 'a"b'}, {"x": "a\nb"}]
    for override in invalid:
        try:
            builder.validate_settings_override(override)
        except ValueError:
            pass
        else:
            raise AssertionError("accepted invalid override: %r" % override)
    for item in ("no-equals", "=missing-key"):
        try:
            cli._load_settings_override(None, [item])
        except ValueError:
            pass
        else:
            raise AssertionError("accepted invalid --settings")


def test_run_cli_rejects_bad_json_shape_before_backend():
    with tempdir() as td, contextlib.redirect_stderr(io.StringIO()):
        path = Path(td) / "settings.json"
        path.write_text('[1,2]')
        assert cli.main(["run", "not-read.wh.cpp", "--settings-json", str(path)]) == 2


def test_cli_forwards_override_to_default_and_explicit_backend():
    with patch("awt.cli.runner.run", return_value=fake_report()) as run:
        assert cli.main(["run", "mod.wh.cpp", "--settings", "retries=8"]) == 0
        assert run.call_args.kwargs["settings_override"] == {"retries": 8}
    from awt import backends
    with patch.object(backends, "get") as get:
        get.return_value.run.return_value = fake_report()
        assert cli.main(["run", "mod.wh.cpp", "--backend", "wine", "--settings", "enabled=false"]) == 0
        assert get.return_value.run.call_args.kwargs["settings_override"] == {"enabled": 0}


def test_batch_discovery_is_recursive_and_deduplicated():
    with tempdir() as td:
        root = Path(td)
        (root / "sub").mkdir()
        a, b = root / "a.wh.cpp", root / "sub/b.wh.cpp"
        a.touch(); b.touch(); (root / "ignore.txt").touch()
        assert batch.discover_mods([str(root), str(a)]) == [str(a), str(b)]
        assert batch.discover_mods([str(root / "*.no-match")]) == [str(root / "*.no-match")]


def test_batch_continues_after_exception_and_escapes_junit():
    calls = []
    def run(path, **kwargs):
        calls.append(path)
        if path == "bad<&.wh.cpp":
            raise RuntimeError("problem < & >")
        return fake_report()
    result = batch.run_batch(["good", "bad<&.wh.cpp", "last"], run)
    assert calls == ["good", "bad<&.wh.cpp", "last"]
    assert batch.batch_exit_code(result) == 1
    xml = ET.fromstring(batch.junit_xml(result))
    assert xml.attrib["failures"] == "1"
    assert "< & >" in xml.findall("testcase")[1].find("failure").text


def test_batch_all_verdicts_and_mixed_skips_fail_closed():
    for level, expected in [("loads_no_crash", 0), ("public_hooks_verified", 0),
                            ("build_failed", 1), ("load_failed", 1), ("crashed", 1),
                            ("unknown", 1), ("not_executed", 3)]:
        result = batch.run_batch([level], lambda _: fake_report(level))
        assert batch.batch_exit_code(result) == expected
        xml = ET.fromstring(batch.junit_xml(result))
        assert int(xml.attrib["skipped"]) == (expected == 3)
        assert int(xml.attrib["failures"]) == (expected == 1)
    result = batch.run_batch(["loads_no_crash", "not_executed"], lambda level: fake_report(level))
    assert batch.batch_exit_code(result) == 3
    assert batch.batch_exit_code(batch.run_batch([], lambda _: None)) == 2


def test_batch_timeout_fails_even_if_legacy_verdict_did_not():
    result = batch.run_batch(["mod"], lambda _: fake_report(timed_out=True))
    assert result["results"][0]["level"] == "loads_no_crash"
    assert batch.batch_exit_code(result) == 1
    assert ET.fromstring(batch.junit_xml(result)).attrib["failures"] == "1"


def test_batch_cli_empty_directory_and_exports():
    with tempdir() as td, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert cli.main(["batch", td]) == 2
        root = Path(td)
        (root / "one.wh.cpp").touch()
        with patch("awt.runner.run", return_value=fake_report()):
            assert cli.main(["batch", td, "--json", str(root / "out.json"), "--junit", str(root / "out.xml")]) == 0
        assert json.loads((root / "out.json").read_text())["summary"]["mods"] == 1
        assert ET.parse(root / "out.xml").getroot().attrib["tests"] == "1"


def test_missing_toolchain_still_reports_static_checks_and_effective_settings():
    from awt import runner
    with patch.object(builder, "missing_toolchain", return_value=["test compiler"]), \
         patch("awt.report.finish", side_effect=lambda rep, hist: rep), \
         patch("awt.runner.host_env.probe", return_value={}):
        rep = runner.run(str(REPO / "examples/settings-demo.wh.cpp"),
                         settings_override={"enabled": False, "typo": 7})
    assert rep["verdict"]["level"] == "not_executed"
    assert rep["verdict"]["verified"] == []
    assert rep["build"]["effective_settings"]["enabled"] == 0
    assert rep["build"]["settings_override_unknown"] == ["typo"]
    assert rep["static_checks"]["summary"]["warnings"] == 1


def test_vm_refuses_override_without_starting_a_guest():
    from awt.backends.vm import VmBackend
    backend = VmBackend()
    with patch.object(backend, "config", side_effect=AssertionError("VM config should not be accessed")), \
         patch.object(backend, "environment", return_value={"backend": "self-hosted-vm"}), \
         patch("awt.report.finish", side_effect=lambda rep, hist: rep):
        rep = backend.run(str(REPO / "examples/settings-demo.wh.cpp"), settings_override={"enabled": 0})
    assert rep["verdict"]["level"] == "not_executed"
    assert "overrides" in rep["verdict"]["not_executed_reason"]


def test_host_rebuilds_when_source_changes_without_a_real_compiler():
    import os
    from types import SimpleNamespace
    with tempdir() as td:
        root = Path(td)
        (root / "host").mkdir()
        source = root / "host/host.cpp"
        source.write_text("// fake host source")
        commands = []
        stamp = max(source.stat().st_mtime, Path(builder.__file__).stat().st_mtime) + 10
        def compile_host(command):
            commands.append(command)
            output = Path(command[command.index("-o") + 1])
            output.touch()
            os.utime(output, (stamp, stamp))
            return SimpleNamespace(returncode=0, stderr="")
        with patch.object(builder, "REPO", td), patch.object(builder, "_run", side_effect=compile_host):
            builder.build_host()
            builder.build_host()
            assert len(commands) == 1
            os.utime(source, (stamp + 1, stamp + 1))
            builder.build_host()
            assert len(commands) == 2
        assert "-lgdi32" in commands[0] and "-lshell32" in commands[0]


def test_batch_does_not_accept_unconfirmed_init_or_abnormal_exit():
    for run in ({"init_returned": None}, {"exit_code": 2}, {"exit_code": None}):
        result = batch.run_batch(["mod"], lambda _: fake_report(**run))
        assert batch.batch_exit_code(result) == 1
        assert ET.fromstring(batch.junit_xml(result)).attrib["failures"] == "1"
    rep = fake_report()
    rep["crash"] = {"crashed": True}
    assert batch.batch_exit_code(batch.run_batch(["mod"], lambda _: rep)) == 1
