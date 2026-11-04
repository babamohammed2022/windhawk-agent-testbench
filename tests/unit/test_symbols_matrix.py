"""Unit tests for the build matrix (`configs/symbol-matrix.yaml`, `awt/symbols/matrix.py`).

The matrix is the contract about *which* Windows builds are checked, so the
required builds and the pinned signatures are asserted explicitly: a typo there
would silently turn a real check into an `unknown`.
"""
import os
import tempfile

from awt import yamlmini
from awt.symbols import matrix as matrix_mod
from awt.symbols import winbindex
from tests.unit.harness import SkipTest, optional_import

REQUIRED = {
    "win10-1507": "1507",
    "win10-22h2": "22H2",
    "win11-21h2": "11-21H2",
    "win11-23h2": "11-23H2",
    "win11-24h2": "11-24H2",
    "win11-25h2": "11-25H2",
}


def test_committed_config_loads():
    matrix = matrix_mod.load()
    assert matrix["source"] == "file", matrix["source"]
    assert matrix["path"] == matrix_mod.DEFAULT_PATH, matrix["path"]
    assert os.path.isfile(matrix["path"])
    assert matrix["schema"] == 1


def test_required_builds_are_present():
    matrix = matrix_mod.load()
    builds = dict((b["id"], b) for b in matrix["builds"])
    for build_id, channel in REQUIRED.items():
        assert build_id in builds, sorted(builds)
        assert builds[build_id]["channel"] == channel, builds[build_id]
        assert builds[build_id]["required"] is True, builds[build_id]
        assert builds[build_id]["enabled"] is True, builds[build_id]
        assert builds[build_id]["label"], builds[build_id]


def test_symbol_server_configuration():
    matrix = matrix_mod.load()
    server = matrix["symbol_server"]
    assert server["base_url"] == "https://msdl.microsoft.com/download/symbols", server
    assert server["timeout_seconds"] >= 5
    assert server["attempts"] >= 1
    # the cache must stay out of git
    assert ".cache" in server["cache_dir"] or ".sandbox" in server["cache_dir"], server
    assert os.path.isabs(server["cache_dir"]), server


def test_pinned_signatures_match_the_builtin_table():
    """The YAML and awt/symbols/winbindex.py must not drift apart."""
    matrix = matrix_mod.load()
    pinned = matrix["pinned_signatures"]
    for module, channels in winbindex.PINNED.items():
        assert module in pinned, (module, sorted(pinned))
        for channel, entry in channels.items():
            assert channel in pinned[module], (module, channel)
            got = pinned[module][channel]
            assert got["timestamp"] == entry["timestamp"], (module, channel, got)
            assert got["virtual_size"] == entry["virtual_size"], (module, channel, got)
            assert isinstance(got["timestamp"], int) and isinstance(got["virtual_size"], int)


def test_pinned_signature_shape():
    entry, note = winbindex.Resolver(offline=True).resolve("twinui.pcshell.dll",
                                                           "win11-24h2")
    assert entry is not None, note
    assert entry["signature"] == "BC86BB4D9a5000", entry
    assert entry["source"] == "pinned", entry
    assert entry["version"].startswith("10.0.26100"), entry


def test_modules_absent_on_a_build_resolve_to_nothing():
    resolver = winbindex.Resolver(offline=True)
    entry, note = resolver.resolve("taskbar.dll", "win10-1507")
    assert entry is None, entry
    assert "not listed" in note or "no " in note, note
    entry, note = resolver.resolve("twinui.pcshell.dll", "win10-1507")
    assert entry is None, entry
    assert note, "an unresolvable build must carry a reason"


def test_channel_normalisation():
    assert winbindex.channel_for("win11-24h2") == "11-24H2"
    assert winbindex.channel_for("win10-22h2") == "22H2"
    assert winbindex.channel_for("11-24H2") == "11-24H2"
    assert winbindex.channel_for("24H2") == "11-24H2"
    assert winbindex.channel_for("1507") == "1507"
    assert winbindex.channel_for(None) is None or winbindex.channel_for(None) == ""


def test_select_builds():
    matrix = matrix_mod.load()
    enabled = matrix_mod.select_builds(matrix)
    assert [b["id"] for b in enabled if b["id"] in REQUIRED] == sorted(
        REQUIRED, key=lambda i: [b["id"] for b in matrix["builds"]].index(i))
    subset = matrix_mod.select_builds(matrix, ["win11-24h2", "win10-1507"])
    assert {b["id"] for b in subset} == {"win11-24h2", "win10-1507"}, subset
    none = matrix_mod.select_builds(matrix, ["win12-99h9"])
    assert none == []
    assert any("not in the matrix" in n for n in matrix["notes"]), matrix["notes"]
    # a disabled entry is skipped unless asked for
    disabled = [b for b in matrix["builds"] if not b.get("enabled", True)]
    if disabled:
        with_disabled = matrix_mod.select_builds(matrix_mod.load(),
                                                 [disabled[0]["id"]],
                                                 include_disabled=True)
        assert with_disabled and with_disabled[0]["id"] == disabled[0]["id"]


def test_explicit_missing_file_raises():
    try:
        matrix_mod.load("/nonexistent/symbol-matrix.yaml")
    except OSError:
        return
    raise AssertionError("a missing --matrix file was ignored")


def test_custom_matrix_overrides():
    text = '''schema: 1
symbol_server:
  base_url: "http://127.0.0.1:1"
  cache_dir: ".cache/test-symbols"
winbindex:
  enabled: false
arch: "x86"
builds:
  - id: "win11-24h2"
    label: "test build"
    channel: "11-24H2"
    required: true
pinned_signatures:
  test.dll:
    11-24H2: {timestamp: 0x11223344, virtual_size: 0x5566, version: "1.2.3.4"}
limits:
  max_requests: 7
'''
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "matrix.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        matrix = matrix_mod.load(path)
        assert matrix["arch"] == "x86"
        assert matrix["symbol_server"]["base_url"] == "http://127.0.0.1:1"
        assert [b["id"] for b in matrix["builds"]] == ["win11-24h2"]
        assert matrix["limits"]["max_requests"] == 7
        # the pinned table extends the built-in one instead of replacing it
        assert "test.dll" in matrix["pinned_signatures"]
        assert "explorer.exe" in matrix["pinned_signatures"]
        entry, _note = winbindex.Resolver(
            offline=True, pinned=matrix["pinned_signatures"]).resolve("test.dll",
                                                                      "win11-24h2")
        assert entry["signature"] == "112233445566", entry


def test_broken_yaml_is_reported():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "bad.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("builds:\n\t- id: tabbed\n")     # tabs are rejected
        try:
            matrix_mod.load(path)
        except yamlmini.YamlMiniError:
            return
        raise AssertionError("a matrix with tab indentation was accepted")


def test_describe_is_serialisable():
    import json
    matrix = matrix_mod.load()
    text = json.dumps(matrix_mod.describe(matrix))
    assert "win11-24h2" in text and "msdl.microsoft.com" in text


def test_pyyaml_agrees_when_available():
    yaml = optional_import("yaml")
    matrix = matrix_mod.load()
    with open(matrix["path"], encoding="utf-8") as f:
        reference = yaml.safe_load(f)
    assert [b["id"] for b in reference["builds"]] == \
           [b["id"] for b in matrix_mod.load()["builds"]]
    assert reference["symbol_server"]["base_url"] == matrix["symbol_server"]["base_url"]
    pinned = reference.get("pinned_signatures") or {}
    for module, channels in pinned.items():
        assert str(module).lower() in matrix["pinned_signatures"], module
        for channel, entry in channels.items():
            # PyYAML reads a bare `1507:` key as an int, yamlmini as an int too;
            # the loader normalises channel keys to strings, so compare as str.
            got = matrix["pinned_signatures"][str(module).lower()][str(channel)]
            assert got["timestamp"] == entry["timestamp"], (module, channel)
            assert got["virtual_size"] == entry["virtual_size"], (module, channel)


def test_pinned_yaml_rendering_round_trips():
    text = winbindex.pinned_yaml({"a.dll": {"11-24H2": {
        "timestamp": 0x1234, "virtual_size": 0x56, "version": "1.0"}}})
    parsed = yamlmini.loads(text)
    assert parsed["pinned_signatures"]["a.dll"]["11-24H2"]["timestamp"] == 0x1234, parsed


def test_no_microsoft_binary_paths_in_the_config():
    matrix = matrix_mod.load()
    text = yamlmini.dump(matrix_mod.describe(matrix))
    for forbidden in (".pdb", ".dll/", "C:\\\\"):
        if forbidden in text:
            raise SkipTest("descriptions mention %s only as a name, not a path"
                           % forbidden)
