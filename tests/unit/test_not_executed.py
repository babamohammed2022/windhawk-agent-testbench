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
    missing = builder.missing_toolchain()
    result = builder.build(os.path.join(REPO, "examples", "hello-log.wh.cpp"))
    assert result["ok"] is False
    assert result["toolchain_missing"] == missing
    if missing:
        assert "build not attempted" in result["errors"][0]["message"]
        assert "nothing was compiled or executed" in result["errors"][0]["message"]


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
