"""yamlmini must agree with PyYAML on every configuration file shipped here."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)

from awt import yamlmini
from tests.unit.harness import SkipTest

try:
    import yaml as pyyaml
except ImportError:  # pragma: no cover - optional accelerator
    pyyaml = None

FIXTURE = """
# a comment
version: 1
server: "https://msdl.microsoft.com/download/symbols"
enabled: true
missing: null
ratio: 0.92
count: 42
hexvalue: 0xBC86BB4D
tags: [shell, taskbar, classic]
inline: {a: 1, b: two}
builds:
  - id: win11-24h2
    label: Windows 11 24H2
    build_number: 26100
    modules:
      - name: twinui.pcshell.dll
        pe:
          timestamp: 0xBC86BB4D
          size_of_image: 0x9A5000
      - name: explorer.exe
        pe: {timestamp: 0xBEE7E13F, size_of_image: 0x344000}
  - id: win10-1507
    modules: []
nested:
  a:
    b:
      - 1
      - 2
      - deep: true
"""


def test_fixture_matches_pyyaml():
    if pyyaml is None:
        raise SkipTest("PyYAML not installed (cross-check unavailable)")
    assert yamlmini.loads(FIXTURE) == pyyaml.safe_load(FIXTURE)


def test_scalar_types():
    data = yamlmini.loads(FIXTURE)
    assert data["version"] == 1 and isinstance(data["version"], int)
    assert data["enabled"] is True
    assert data["missing"] is None
    assert data["ratio"] == 0.92
    assert data["hexvalue"] == 0xBC86BB4D
    assert data["server"] == "https://msdl.microsoft.com/download/symbols"
    assert data["tags"] == ["shell", "taskbar", "classic"]
    assert data["inline"] == {"a": 1, "b": "two"}
    assert data["builds"][1]["modules"] == []
    assert data["nested"]["a"]["b"][2] == {"deep": True}


def test_comments_and_quotes():
    data = yamlmini.loads('a: 1  # trailing comment\n'
                          'b: "has # hash"\n'
                          "c: 'single'\n")
    assert data == {"a": 1, "b": "has # hash", "c": "single"}


def test_empty_document():
    assert yamlmini.loads("") == {}
    assert yamlmini.loads("# only a comment\n\n") == {}


def test_rejects_unsupported_constructs():
    for bad in ("a: |\n  block scalar\n", "a:\n\tb: 1\n"):
        try:
            yamlmini.loads(bad)
        except yamlmini.YamlMiniError:
            continue
        raise AssertionError("expected YamlMiniError for %r" % bad)


def test_dump_roundtrip():
    data = yamlmini.loads(FIXTURE)
    text = yamlmini.dump(data)
    assert yamlmini.loads(text) == data
    if pyyaml is not None:
        assert pyyaml.safe_load(text) == data


def _coerce_bool_keys(mini, other):
    """Undo PyYAML's YAML 1.1 boolean *keys* so both parsers can be compared.

    PyYAML turns the mapping key `on:` (used by GitHub Actions workflows) into
    `True`; yamlmini follows YAML 1.2 and keeps the string "on". Only keys are
    coerced, and only where yamlmini saw one of the YAML 1.1 boolean words, so a
    genuine disagreement in values is still reported.
    """
    bool_words = {"on", "off", "yes", "no", "y", "n", "true", "false"}
    if isinstance(mini, dict) and isinstance(other, dict):
        out = {}
        for key, value in other.items():
            fixed = key
            if isinstance(key, bool) or (isinstance(key, str) and key.lower() in bool_words):
                text = {True: "on", False: "off"}.get(key)
                if isinstance(key, str):
                    text = key
                if text is not None and any(
                        isinstance(mk, str) and mk.lower() == text.lower() for mk in mini):
                    fixed = next(mk for mk in mini
                                 if isinstance(mk, str) and mk.lower() == text.lower())
            out[fixed] = _coerce_bool_keys(mini.get(fixed, mini.get(key)), value)
        return out
    if isinstance(mini, list) and isinstance(other, list):
        return [_coerce_bool_keys(m, o) for m, o in zip(mini, other)]
    return other


def test_shipped_configs_parse_and_match_pyyaml():
    """Every YAML file committed in this repo must parse with the bundled reader."""
    configs = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in (".git", ".sandbox", ".cache",
                                                "third_party", "__pycache__", "node_modules")]
        for f in files:
            if f.endswith((".yaml", ".yml")):
                configs.append(os.path.join(root, f))
    if not configs:
        raise SkipTest("no YAML configuration files committed yet")
    for path in configs:
        text = open(path, encoding="utf-8").read()
        data = yamlmini.loads(text)
        assert isinstance(data, (dict, list)), path
        if pyyaml is not None:
            reference = _coerce_bool_keys(data, pyyaml.safe_load(text))
            assert data == reference, "yamlmini disagrees with PyYAML: %s" % path
