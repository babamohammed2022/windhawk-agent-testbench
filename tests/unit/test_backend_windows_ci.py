"""Tests for the native Windows (CI) backend.

This backend cannot execute on the Linux machine where the bench is developed, so
what is tested here is exactly what can be tested honestly:

* the environment-detection helpers are pure functions and are checked against
  the strings Windows really produces (`ver` output, registry values, GitHub
  Actions variables);
* the backend refuses to run off Windows and produces a structured
  `not_executed` report that keeps the v1 shape;
* a Windows Server environment is flagged indicative, a client one is not.

The real native execution path is exercised by `.github/workflows/windows-ci.yml`
on a `windows-latest` runner; its report is uploaded as an artifact.
"""
import contextlib
import io
import json
import os
import struct
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tests.unit.harness import SkipTest, require            # noqa: E402
from tests.unit.fixtures import fake_vm                     # noqa: E402
from awt.backends import base, windows_ci                   # noqa: E402

EXAMPLE = os.path.join(REPO, "examples", "hello-log.wh.cpp")


def _png(path, rows):
    """Write a PNG from a list of rows of (r, g, b) tuples."""
    height = len(rows)
    width = len(rows[0])
    raw = b"".join(b"\x00" + b"".join(bytes(px) for px in row) for row in rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9))
                + chunk(b"IEND", b""))
    return path


def test_parse_windows_ver_command_output():
    got = windows_ci.parse_windows_ver(
        "Microsoft Windows [Version 10.0.20348.1234]")
    require(got == {"os_version": "10.0.20348", "build_number": 20348,
                    "revision": 1234},
            "ver output parsed wrong: %r" % got)
    got = windows_ci.parse_windows_ver("Microsoft Windows [Version 6.1.7601]")
    require(got["build_number"] == 7601 and got["revision"] is None,
            "Windows 7 SP1 output parsed wrong: %r" % got)


def test_parse_windows_ver_accepts_the_registry_form():
    got = windows_ci.parse_windows_ver("10.0.20348")
    require(got["build_number"] == 20348 and got["revision"] is None,
            "the registry CurrentVersion form must parse: %r" % got)


def test_parse_windows_ver_rejects_garbage():
    for text in ("", None, "Linux 6.1.0", "no digits here", "Windows 10 Pro",
                 "6.1.0-13-amd64"):
        require(windows_ci.parse_windows_ver(text) is None,
                "%r must not produce a Windows build number" % text)


def test_product_type_from_registry():
    cases = [("Client", 1), ("Server", 3), ("Server Core", None),
             ("WinNT", 1), ("LanmanNT", 3), (1, 1), (3, 3), (9, None), (None, None)]
    for value, expected in cases:
        got = windows_ci.product_type_from_registry(value)
        require(got == expected, "ProductType %r -> %r, expected %r"
                % (value, got, expected))


def test_installation_type_to_profile():
    require(windows_ci.installation_type_to_profile("Client") == "client",
            "Client installation type")
    require(windows_ci.installation_type_to_profile("Server") == "server",
            "Server installation type")
    require(windows_ci.installation_type_to_profile("Server Core") == "server",
            "Server Core is still a server shell")
    require(windows_ci.installation_type_to_profile("") is None,
            "an empty InstallationType must not be guessed")
    require(windows_ci.installation_type_to_profile(None) is None, "None")


def test_edition_for_known_builds():
    require(windows_ci.edition_for(20348) == "Windows Server 2022",
            "windows-latest is Server 2022")
    require(windows_ci.edition_for(22000) == "Windows 11 21H2", "22000")
    require(windows_ci.edition_for(19045) == "Windows 10 22H2", "19045")
    require(windows_ci.edition_for(7601).startswith("Windows 7"),
            "Windows 7 Thin PC build 7601")


def test_edition_prefers_what_windows_reports():
    got = windows_ci.edition_for(22000, "Windows 11 Pro", "21H2")
    require(got == "Windows 11 Pro 21H2", "ProductName should win: %r" % got)
    got = windows_ci.edition_for(20348, "Windows Server 2022 Standard", None)
    require(got == "Windows Server 2022 Standard", "ProductName alone: %r" % got)


def test_edition_for_unknown_build_is_none():
    require(windows_ci.edition_for(99999) is None, "an unknown build must not be named")
    require(windows_ci.edition_for(None) is None, "no build -> no edition")
    require(windows_ci.edition_for("abc") is None, "a non-numeric build -> no edition")


def test_github_runner_info_detects_actions():
    env = {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Windows",
           "ImageOS": "windows-2022", "ImageVersion": "20260115.2",
           "RUNNER_NAME": "Hosted Agent", "GITHUB_REPOSITORY": "o/r",
           "GITHUB_RUN_ID": "41877151890"}
    info = windows_ci.github_runner_info(env)
    require(info["is_github"] is True, "GITHUB_ACTIONS=true not detected")
    require(info["image_os"] == "windows-2022", "runner image not read")
    require(info["run_id"] == "41877151890", "run id not read")


def test_github_runner_info_is_empty_off_ci():
    require(windows_ci.github_runner_info({}) == {},
            "off CI there is nothing to report")
    require(windows_ci.github_runner_info({"GITHUB_ACTIONS": "false"}) == {},
            "GITHUB_ACTIONS=false must not read as CI")


def test_detect_environment_off_windows_is_honest():
    if os.name == "nt":
        raise SkipTest("this asserts the non-Windows behaviour")
    env = windows_ci.detect_environment()
    require(env["backend"] == "windows-runner", "backend name")
    require(env["os"] is None, "claiming a Windows OS off Windows")
    require(env["build_number"] is None, "inventing a build number off Windows")
    require(env["shell_profile"] == "unknown",
            "off Windows the shell profile must stay unknown")
    require(any("not collected on Windows" in note for note in env["notes"]),
            "the environment must say it was not collected on Windows: %r"
            % env["notes"])
    require(base.indicative_notes(env), "an unknown environment needs a caveat")


def test_backend_is_unavailable_off_windows_with_a_reason():
    if os.name == "nt":
        raise SkipTest("this asserts the non-Windows behaviour")
    ok, reason = windows_ci.WindowsRunnerBackend().available()
    require(ok is False, "the native backend claimed availability on Linux")
    require("not running on Windows" in reason, "the reason must be explicit: %r"
            % reason)
    require("Linux" in reason, "the reason should say where it is: %r" % reason)


def test_run_off_windows_is_not_executed_and_keeps_v1_shape():
    if os.name == "nt":
        raise SkipTest("on Windows this would try to build and run for real")
    require(os.path.exists(EXAMPLE), "the example mod is missing")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rep = windows_ci.WindowsRunnerBackend().run(EXAMPLE, seconds=1)
    for key in ("schema", "mod", "build", "analysis", "include_analysis", "run",
                "environment", "shim", "verdict", "artifacts"):
        require(key in rep, "v1 key %r missing from the backend report" % key)
    require(rep["verdict"]["level"] == "not_executed",
            "off Windows the level must be not_executed, got %r"
            % rep["verdict"]["level"])
    require(rep["run"]["backend"] == "windows-runner",
            "the backend must name itself even when it could not run")
    require("not running on Windows" in rep["verdict"]["not_executed_reason"],
            "the reason must reach the verdict")
    require(rep["run"]["environment"]["shell_profile"] == "unknown",
            "run.environment missing or wrong")
    require(rep["verdict"]["verified"] == [], "nothing ran, nothing is verified")
    require(rep["mod"]["id"], "the mod identity should still be parsed")
    require(json.loads(json.dumps(rep)) is not None, "report is not JSON-clean")


def test_windows_server_environment_is_flagged_indicative():
    env = base.environment_block(backend="windows-runner", os="Windows",
                                 os_version="10.0.20348", build_number=20348,
                                 product_type="server", shell_profile="server",
                                 edition="Windows Server 2022", vm_used=True)
    notes = base.indicative_notes(env)
    require(len(notes) == 1, "expected exactly one caveat: %r" % notes)
    require("INDICATIVE" in notes[0] and "Windows Server" in notes[0],
            "the note must say indicative and name Server: %r" % notes[0])
    require("20348" in notes[0], "the note should carry the build number")


def test_client_windows_environment_needs_no_caveat():
    env = base.environment_block(backend="self-hosted-vm", os="Windows",
                                 build_number=22000, product_type="client",
                                 shell_profile="client", vm_used=True)
    require(base.indicative_notes(env) == [],
            "a real client Windows run must not be watered down")


def test_github_runner_environment_is_recorded():
    """What the report will contain when the workflow runs on windows-latest."""
    env = base.environment_block(
        backend="windows-runner", os="Windows", build_number=20348,
        product_type="server", shell_profile="server", vm_used=True,
        runner="Hosted Agent", runner_image="windows-2022",
        notes=[windows_ci.GITHUB_RUNNER_NOTE])
    require(env["vm_used"] is True, "a GitHub runner is a VM and must say so")
    require(env["runner_image"] == "windows-2022", "runner image not recorded")
    require(any("GitHub-hosted runner" in note for note in env["notes"]),
            "the runner note is missing")


def test_take_screenshot_off_windows_is_unavailable():
    if os.name == "nt":
        raise SkipTest("off Windows only")
    path, note = windows_ci.WindowsRunnerBackend().take_screenshot("/tmp/x.png")
    require(path is None, "a screenshot cannot be taken off Windows")
    require("native Windows session" in note, "the note must explain: %r" % note)


def test_looks_blank_detects_a_single_colour_capture():
    tmp = tempfile.mkdtemp(prefix="awt-shot-")
    blank = os.path.join(tmp, "blank.png")
    fake_vm.write_png(blank, width=8, height=6, rgb=(0, 0, 0))
    varied = os.path.join(tmp, "varied.png")
    _png(varied, [[(0, 0, 0), (255, 255, 255)], [(128, 12, 200), (7, 240, 33)]])
    try:
        from PIL import Image                          # noqa: F401
    except ImportError:
        require(windows_ci._looks_blank(blank) is None,
                "without Pillow the question must stay unanswered, not guessed")
        return
    require(windows_ci._looks_blank(blank) is True,
            "a uniform capture (a service desktop) must be flagged")
    require(windows_ci._looks_blank(varied) is False,
            "a varied capture must not be flagged")


def test_selftest_reports_and_exits_zero():
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = windows_ci.selftest()
    text = buffer.getvalue()
    require(code == 0, "the selftest must not fail the job")
    require("windows-runner" in text, "the selftest did not name the backend")
    require("available: no" in text or "available: yes" in text,
            "the selftest must state availability: %r" % text)
    require("shell_profile=" in text, "the selftest must show the shell profile")


def test_module_cli_selftest_and_not_executed_exit_codes():
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        require(windows_ci.main(["--selftest"]) == 0, "--selftest exit code")
    if os.name == "nt":
        raise SkipTest("running the mod needs the non-Windows refusal")
    buffer = io.StringIO()
    tmp = tempfile.mkdtemp(prefix="awt-winci-")
    out = os.path.join(tmp, "report.json")
    with contextlib.redirect_stdout(buffer):
        code = windows_ci.main(["--mod", EXAMPLE, "--seconds", "1", "--json", out])
    require(code == 3, "not_executed must exit 3 (the bench's convention), got %d"
            % code)
    require(os.path.exists(out), "--json did not write the report")
    with open(out, encoding="utf-8") as f:
        saved = json.load(f)
    require(saved["verdict"]["level"] == "not_executed", "saved report level")
    require(saved["run"]["environment"]["backend"] == "windows-runner",
            "saved report environment")


def test_summary_markdown_states_the_level_and_the_caveats():
    rep = {
        "mod": {"id": "hello-log"},
        "run": {"backend": "windows-runner", "exit_code": 0, "crashed": False,
                "environment": {"os": "Windows", "edition": "Windows Server 2022",
                                "build_number": 20348, "shell_profile": "server",
                                "vm_used": True, "runner_image": "windows-2022"}},
        "verdict": {"level": "public_hooks_verified",
                    "verified": ["build", "load", "public API hooks"],
                    "not_verifiable_here": ["internal-symbols: ?X@@YAXXZ"],
                    "indicative": [base.SERVER_SHELL_NOTE.format(build=20348)]},
        "artifacts": {"screenshots": ["/tmp/shot.png"]},
    }
    text = windows_ci.summarise_markdown(rep)
    for needed in ("public_hooks_verified", "Windows Server 2022", "20348",
                   "server", "INDICATIVE", "shim host", "not_verifiable_here",
                   "/tmp/shot.png"):
        require(needed in text, "the CI summary must mention %r:\n%s" % (needed, text))
    require(text.startswith("## "), "the summary must be Markdown")


def test_summary_markdown_for_a_run_that_never_happened():
    backend = windows_ci.WindowsRunnerBackend()
    if os.name == "nt":
        raise SkipTest("off Windows only")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rep = backend.run(EXAMPLE, seconds=1)
    text = windows_ci.summarise_markdown(rep)
    require("`not_executed`" in text, "the level must be visible: %r" % text)
    require("Nothing was executed" in text, "the summary must say nothing ran")
    require("verified" in text and "nothing" in text,
            "the summary must not imply anything was verified")


def test_summary_cli_reads_a_report_file():
    tmp = tempfile.mkdtemp(prefix="awt-summary-")
    path = os.path.join(tmp, "report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"mod": {"id": "x"}, "run": {}, "verdict": {"level": "crashed"},
                   "artifacts": {}}, f)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = windows_ci.main(["--summary", path])
    require(code == 0, "--summary exit code")
    require("`crashed`" in buffer.getvalue(), "the summary did not render the level")


def test_describe_documents_the_shim_host_limit():
    entry = windows_ci.WindowsRunnerBackend().describe()
    require(entry["executes_on"] == "windows", "describe().executes_on")
    require(any("shim host" in text for text in entry["limitations"]),
            "the description must say the Windhawk engine is not involved")
    require(any("Windows Server" in text for text in entry["limitations"]),
            "the description must warn about GitHub runners being Server")
