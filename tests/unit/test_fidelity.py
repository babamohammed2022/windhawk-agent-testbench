"""Offline regression tests for metadata, targeting, flags and report fidelity.

No Wine, Windows or compiler is involved: these tests cover the pure-Python
parsers and the additive report sections. They never upgrade a runtime verdict.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)

from awt import metadata  # noqa: E402

MOD = """// ==WindhawkMod==
// @id              fidelity-demo
// @name            Fidelity demo
// @name:it-IT      Demo fedelta
// @description     Demo
// @version         1.2.3
// @author          awt
// @github          https://example.invalid/awt
// @twitter         @awt
// @homepage        https://example.invalid
// @license         MIT
// @include         C:\\Windows\\System32\\notepad.exe
// @include         explorer.exe
// @exclude         *\\helper.exe
// @compilerOptions -lcomctl32 -std=c++20 -DFOO=1
// @architecture    x86-64
// ==/WindhawkMod==
"""

SETTINGS = """// ==WindhawkModSettings==
/*
- enabled: true
  $name: Enabled
- greeting: "hello sandbox"
  $name: Greeting
- items:
  - size: 4
    label: "first"
  - size: 7
    label: "second"
*/
// ==/WindhawkModSettings==
"""


def test_metadata_keeps_every_documented_tag_and_localisation():
    meta = metadata.parse_metadata(MOD)
    raw = meta["raw"]
    for key, expected in (("id", "fidelity-demo"), ("version", "1.2.3"),
                          ("author", "awt"), ("github", "https://example.invalid/awt"),
                          ("twitter", "@awt"), ("homepage", "https://example.invalid"),
                          ("license", "MIT"), ("architecture", "x86-64")):
        assert raw.get(key) == expected, (key, raw.get(key))
    assert meta["include"] == [r"C:\Windows\System32\notepad.exe", "explorer.exe"]
    assert meta["exclude"] == [r"*\helper.exe"]
    assert meta["compilerOptions"] == ["-lcomctl32", "-std=c++20", "-DFOO=1"]
    assert meta["localizations"] == {"it-IT": {"name": "Demo fedelta"}}
    assert meta["warnings"] == []


def test_metadata_defaults_are_backwards_compatible():
    meta = metadata.parse_metadata("no block here")
    assert meta["include"] == ["*"] and meta["raw"] == {}
    assert metadata.parse_metadata("// ==WindhawkMod==\n// @id x\n").get("raw") == {}


def test_nested_and_indexed_settings_are_flattened_without_display_keys():
    flat, tree, warnings = metadata.parse_settings_ex(SETTINGS)
    assert warnings == []
    assert flat == {"enabled": 1, "greeting": "hello sandbox",
                    "items[0].size": 4, "items[0].label": "first",
                    "items[1].size": 7, "items[1].label": "second"}
    assert tree[2]["items"][1]["size"] == 7
    assert not [k for k in flat if k.startswith("$")]
    assert metadata.parse_settings(SETTINGS) == flat


def test_legacy_flat_settings_and_shipped_examples_still_parse():
    assert metadata.parse_settings("""// ==WindhawkModSettings==
/*
- greeting: "hello sandbox"
  $name: Greeting
*/
// ==/WindhawkModSettings==
""") == {"greeting": "hello sandbox"}
    demo = open(os.path.join(REPO, "examples", "settings-demo.wh.cpp"),
                encoding="utf-8").read()
    assert metadata.parse_settings(demo) == {"enabled": 1, "greeting": "hello sandbox",
                                             "retries": 3}
