"""The environment probe must describe the machine it runs on, never a guess."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from awt import env
from tests.unit.harness import SkipTest


def test_probe_shape():
    probe = env.probe()
    for key in ("host_os", "python", "tools", "python_packages", "cache_dir"):
        assert key in probe, key
    for tool in ("wine", "mingw_cxx", "xvfb", "llvm_pdbutil"):
        assert tool in probe["tools"], tool
    for pkg in ("yaml", "numpy", "PIL", "playwright"):
        assert pkg in probe["python_packages"], pkg


def test_toolchain_ready_matches_probe():
    ready, reason = env.toolchain_ready()
    probe = env.probe()
    missing = [probe["tools"][k] is None for k in ("wine", "mingw_cxx", "xvfb")]
    assert ready == (not any(missing)), (ready, reason)
    if not ready:
        assert "toolchain missing" in reason
        assert "setup.sh" in reason


def test_offline_flag_disables_network_probe():
    old = os.environ.get("AWT_OFFLINE")
    os.environ["AWT_OFFLINE"] = "1"
    try:
        assert env.offline() is True
        net = env.probe_network({"example": "https://msdl.microsoft.com/"})
        assert net["example"]["reachable"] is False
        assert net["example"]["reason"] == "AWT_OFFLINE=1"
    finally:
        if old is None:
            os.environ.pop("AWT_OFFLINE", None)
        else:
            os.environ["AWT_OFFLINE"] = old


def test_probe_network_is_never_fatal():
    """An unreachable host must produce a reason string, not an exception."""
    if env.offline():
        raise SkipTest("AWT_OFFLINE=1")
    net = env.probe_network({"blackhole": "https://127.0.0.1:9/"}, timeout=2.0)
    assert net["blackhole"]["reachable"] is False
    assert net["blackhole"]["reason"]


def test_cache_dir_inside_repo():
    path = env.cache_dir("unit-test")
    assert path.startswith(env.REPO), path
    assert os.path.isdir(path)
    os.rmdir(path)
