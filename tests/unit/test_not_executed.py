"""When no backend can run, the report must say `not_executed` and claim nothing."""
import contextlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)

from awt import builder, report, runner


def test_levels_are_ordered_and_documented():
    assert report.LEVELS[0] == "not_executed"
    assert report.LEVELS[-1] == "public_hooks_verified"
    assert "build_failed" in report.LEVELS


def test_missing_toolchain_reported_structurally():
    """Both sides of the toolchain question: absent means "not attempted", present
    means the build was really attempted (a CI runner has MinGW; this sandbox does
    not, and the assertion must be honest in both places)."""
    missing = builder.missing_toolchain()
    result = builder.build(os.path.join(REPO, "examples", "hello-log.wh.cpp"))
    if missing:
        assert result["ok"] is False
        assert result["toolchain_missing"] == missing
        assert "build not attempted" in result["errors"][0]["message"]
        assert "nothing was compiled or executed" in result["errors"][0]["message"]
    else:
        assert not result.get("toolchain_missing"), (
            "a machine with a toolchain must not report one missing: %r"
            % result.get("toolchain_missing"))
        assert result["ok"] is True or result["errors"], (
            "with a toolchain the build is attempted and reports a real outcome")


def test_missing_runtime_is_checked_before_anything_is_executed():
    """Compiling is not running. Without Wine and a headless display - and on
    Windows at all - `runner.run()` must report not_executed instead of crashing
    inside `subprocess.Popen(["Xvfb", ...])`."""
    absent = runner.missing_runtime()
    if os.name == "nt":
        assert absent and "windows-runner" in absent[0], (
            "on Windows the reason must point at the backend that can run: %r" % absent)
    else:
        assert isinstance(absent, list)
        for name in absent:
            assert name in ("wine", "Xvfb"), "unexpected runtime requirement: %r" % name
    real = runner.missing_runtime
    runner.missing_runtime = lambda: ["wine (simulated absence)"]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            rep = runner.run(os.path.join(REPO, "examples", "hello-log.wh.cpp"))
    finally:
        runner.missing_runtime = real
    if builder.missing_toolchain():
        assert "simulated absence" not in rep["verdict"].get("not_executed_reason", ""), (
            "without a compiler the toolchain reason comes first, which is also honest")
        return
    verdict_block = rep["verdict"]
    assert verdict_block["level"] == "not_executed", verdict_block["level"]
    assert "simulated absence" in verdict_block["not_executed_reason"]
    assert verdict_block["verified"] == [], "nothing ran, nothing is verified"
    assert verdict_block["requires_real_windows"] is False, (
        "not_executed makes no claim at all")
    assert rep["run"]["backend"] == "unavailable"


def test_runner_reports_not_executed_when_toolchain_absent():
    """Force the no-toolchain situation even on a machine that has one."""
    real_missing = builder.missing_toolchain
    real_probe = runner.host_env.probe
    builder.missing_toolchain = lambda: ["MinGW C++ compiler (simulated absence)"]
    runner.host_env.probe = lambda **kw: dict(real_probe(**kw),
                                              tools={"wine": None, "mingw_cxx": None,
                                                     "xvfb": None})
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            rep = runner.run(os.path.join(REPO, "examples", "hello-log.wh.cpp"))
    finally:
        builder.missing_toolchain = real_missing
        runner.host_env.probe = real_probe
    v = rep["verdict"]
    assert v["level"] == "not_executed", v["level"]
    assert v["verified"] == [], "nothing may be claimed as verified"
    assert "simulated absence" in v["not_executed_reason"]
    assert rep["run"]["backend"] == "unavailable"
    assert rep["environment"]["tools"]["wine"] is None
    summary = report.summary(rep)
    assert "not attempted" in summary
    assert "not_executed" in summary


def test_report_file_written_and_history_kept():
    """The report file and the history directory are written by every run.

    This test produces its own report instead of reading whatever a previous
    test happened to leave behind: the runner sorts test functions by name, so a
    dependency on another test's side effect would pass or fail depending on the
    state of .sandbox/ (which is wiped between sandboxes).
    """
    with contextlib.redirect_stdout(io.StringIO()):
        runner.run(os.path.join(REPO, "examples", "hello-log.wh.cpp"))
    rep = json.load(open(os.path.join(REPO, ".sandbox", "report.json"), encoding="utf-8"))
    assert rep["schema"] == 1
    for key in ("mod", "build", "analysis", "run", "verdict", "artifacts", "environment"):
        assert key in rep, key
    hist = os.path.join(REPO, ".sandbox", "history")
    assert os.path.isdir(hist) and os.listdir(hist)
