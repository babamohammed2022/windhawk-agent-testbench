"""End-to-end tests for Feature 1 against a *local* symbol server.

`msdl.microsoft.com` is not reachable from every CI sandbox (and this bench must
not depend on it), so the whole pipeline - resolve signature, fetch the PE, read
its debug directory, fetch the PDB by GUID+age, parse it, match the names - is
exercised against an HTTP server on 127.0.0.1 that serves the synthetic PE/PDB
images from `tests/unit/fixtures`. Everything except Microsoft's bytes is real:
real HTTP, real files on disk, real cache, real PE and PDB parsing.

The offline half of the test is just as important: with no network every cell
must come out `unknown`, never `found` and never `missing`.
"""
import http.server
import json
import os
import socketserver
import tempfile
import threading

from awt import pe as pe_mod
from awt.symbols import cli as symbols_cli
from awt.symbols import table, verify
from tests.unit.fixtures import build_pe, build_pdb, _guid_bytes

MODULE = "twinui.pcshell.dll"
PDB_NAME = "TwinUI.PCShell.pdb"
TIMESTAMP = 0xBC86BB4D
SIZE_OF_IMAGE = 0x9A5000
GUID = _guid_bytes(PDB_NAME)

PDB_SYMBOLS = [
    ("?Invoke@CTaskFrame@@UEAAJXZ", 1, 0x5678),
    ("?ShowStartView@XamlLauncher@@UEAAJW4IMMERSIVELAUNCHERSHOWMETHOD@@"
     "W4IMMERSIVELAUNCHERSHOWFLAGS@@@Z", 1, 0x1234),
    ("DllGetClassObject", 1, 0x9ABC),
    ("?g_pTaskbar@@3PEAVCTaskbar@@EA", 3, 0x100),
]

MOD_SOURCE = r'''// ==WindhawkMod==
// @id           symbol-matrix-selftest
// @name           Symbol matrix self test
// @version        1.0
// @target         explorer.exe
// @include        twinui.pcshell.dll
// @include        Taskbar.dll
// @architecture   x86-64
// ==/WindhawkMod==

#include <windhawk_api.h>

long Invoke_Hook();
long ShowStartView_Hook(int, int);

WindhawkUtils::SYMBOL_HOOK pcshellHooks[] = {
    {
        {L"?Invoke@CTaskFrame@@UEAAJXZ"},
        &Invoke_Original,
        Invoke_Hook,
        false
    },
    {
        {L"public: virtual long __cdecl CTaskFrame::Invoke(void)"},
        &Invoke2_Original,
        Invoke_Hook,
        /* optional */ true
    },
    {
        {L"?DoesNotExist@CTaskFrame@@UEAAJXZ"},
        &Nope_Original,
        Nope_Hook,
        false
    },
    {
        {L"DllGetClassObject"},
        &Dll_Original,
        Dll_Hook,
        false
    },
    {
        {L"?g_pTaskbar@@3PEAVCTaskbar@@EA"},
        &Taskbar_Original,
        nullptr,
        false
    },
};

BOOL Wh_ModInit() {
    HMODULE pcshell = LoadLibrary(L"twinui.pcshell.dll");
    WindhawkUtils::HookSymbols(pcshell, pcshellHooks, ARRAYSIZE(pcshellHooks));
    return TRUE;
}
'''


def _make_handler(root):
    """Serves <root>/<name>/<signature>/<name>, like a symbol store does."""

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            # `directory` has to be passed to __init__: a class attribute of the
            # same name is overwritten by the base class otherwise.
            super().__init__(*args, directory=root, **kwargs)

        def log_message(self, *args):        # keep the test output readable
            pass

    return Handler


class FakeSymbolServer:
    def __init__(self, root):
        self.root = root
        self.handler = _make_handler(root)
        self.httpd = socketserver.TCPServer(("127.0.0.1", 0), self.handler)
        self.httpd.allow_reuse_address = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        return "http://127.0.0.1:%d" % self.port

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def _publish(root, module=MODULE, pdb_name=PDB_NAME, timestamp=TIMESTAMP,
             size_of_image=SIZE_OF_IMAGE, guid=GUID, age=1, symbols=None,
             with_debug_directory=True):
    """Lay out a symbol store tree for one (PE, PDB) pair."""
    pe_bytes = build_pe(timestamp=timestamp, size_of_image=size_of_image,
                        pdb_name=pdb_name, guid=guid, age=age,
                        with_debug_directory=with_debug_directory)
    pe_dir = os.path.join(root, module, pe_mod.pe_signature(timestamp, size_of_image))
    os.makedirs(pe_dir, exist_ok=True)
    with open(os.path.join(pe_dir, module), "wb") as f:
        f.write(pe_bytes)
    image = pe_mod.read_bytes(pe_bytes)
    cv = image.codeview()
    if cv:
        pdb_dir = os.path.join(root, cv["pdb_name"], cv["signature"])
        os.makedirs(pdb_dir, exist_ok=True)
        with open(os.path.join(pdb_dir, cv["pdb_name"]), "wb") as f:
            f.write(build_pdb(module=module, guid=guid, age=age,
                              public_symbols=symbols if symbols is not None
                              else PDB_SYMBOLS))
    return cv


def _matrix_file(tmp, base_url, cache_dir, builds=None, pinned=None):
    builds = builds or [{"id": "win11-24h2", "label": "Windows 11 24H2 (fixture)",
                         "channel": "11-24H2"}]
    lines = ["schema: 1",
             "symbol_server:",
             '  base_url: "%s"' % base_url,
             '  cache_dir: "%s"' % cache_dir,
             "  timeout_seconds: 10",
             "  attempts: 1",
             "winbindex:",
             "  enabled: false",
             '  cache_dir: "%s"' % os.path.join(cache_dir, "winbindex"),
             'arch: "x86-64"',
             "builds:"]
    for build in builds:
        lines.append('  - id: "%s"' % build["id"])
        lines.append('    label: "%s"' % build.get("label", build["id"]))
        lines.append('    channel: "%s"' % build["channel"])
        lines.append("    required: true")
    lines.append("pinned_signatures:")
    for module, channels in (pinned or {MODULE: {"11-24H2": {
            "timestamp": TIMESTAMP, "virtual_size": SIZE_OF_IMAGE,
            "version": "10.0.26100.1"}}}).items():
        lines.append("  %s:" % module)
        for channel, entry in channels.items():
            lines.append("    %s: {timestamp: %d, virtual_size: %d, version: %s}"
                         % (channel, entry["timestamp"], entry["virtual_size"],
                            '"%s"' % entry.get("version")))
    lines.append("limits:")
    lines.append("  sleep_between_downloads: 0")
    path = os.path.join(tmp, "matrix.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _mod_file(tmp, source=MOD_SOURCE):
    path = os.path.join(tmp, "symbol-matrix-selftest.wh.cpp")
    with open(path, "w", encoding="utf-8") as f:
        f.write(source)
    return path


def _rows(section):
    return dict((row["symbol"], row) for row in section["symbols"])


def test_full_pipeline_against_a_local_symbol_server():
    with tempfile.TemporaryDirectory() as tmp:
        store = os.path.join(tmp, "store")
        os.makedirs(store)
        cv = _publish(store)
        assert cv, "the fixture PE must carry a CodeView record"
        server = FakeSymbolServer(store)
        try:
            cache = os.path.join(tmp, "cache")
            matrix = _matrix_file(tmp, server.base_url, cache)
            mod = _mod_file(tmp)
            section = verify.verify_mod(mod, matrix_path=matrix)

            assert section["mod_id"] == "symbol-matrix-selftest", section["mod_id"]
            assert section["arch"] == "x86-64"
            assert section["disclaimer"], "the section must carry its disclaimer"
            rows = _rows(section)

            exact = rows["?Invoke@CTaskFrame@@UEAAJXZ"]["builds"]["win11-24h2"]
            assert exact["outcome"] == "found", exact
            assert exact["confidence"] == "exact", exact
            assert exact["rva"] == 0x1000 + 0x5678, hex(exact["rva"] or 0)

            structural = rows["public: virtual long __cdecl CTaskFrame::Invoke(void)"
                              ]["builds"]["win11-24h2"]
            assert structural["outcome"] == "found", structural
            assert structural["confidence"] == "structural", structural

            absent = rows["?DoesNotExist@CTaskFrame@@UEAAJXZ"]["builds"]["win11-24h2"]
            assert absent["outcome"] == "missing", absent
            assert any("PDB was read" in n for n in absent["notes"]), absent

            export = rows["DllGetClassObject"]["builds"]["win11-24h2"]
            assert export["outcome"] == "found", export

            data = rows["?g_pTaskbar@@3PEAVCTaskbar@@EA"]["builds"]["win11-24h2"]
            assert data["outcome"] == "found", data

            counts = section["summary"]["row_outcomes"]
            assert counts["found"] == 4 and counts["missing"] == 1, counts
            assert section["summary"]["optional_symbols_missing"] == 0, section["summary"]

            module = section["modules"][0]
            assert module["available"] is True, module
            assert module["pe"]["signature"] == "BC86BB4D9a5000", module["pe"]
            assert module["pe"]["downloaded"] is True, module["pe"]
            assert module["pdb"]["signature"] == cv["signature"], module["pdb"]
            assert module["symbol_count"] >= len(PDB_SYMBOLS), module
            assert section["network"]["symbol_server"]["stats"]["downloads"] == 2, \
                section["network"]["symbol_server"]

            text = table.render(section, max_rows=0)
            assert "found*" in text and "MISSING" in text and "found~" in text, text
            row_lines = [l for l in text.split("\n") if "[twinui.pcshell.dll]" in l]
            assert row_lines, text
            assert not any("unknown" in l for l in row_lines), row_lines
            json.dumps(section)                     # the report must be serialisable
        finally:
            server.stop()


def test_cache_serves_a_second_run_without_a_server():
    with tempfile.TemporaryDirectory() as tmp:
        store = os.path.join(tmp, "store")
        os.makedirs(store)
        _publish(store)
        server = FakeSymbolServer(store)
        cache = os.path.join(tmp, "cache")
        matrix = _matrix_file(tmp, server.base_url, cache)
        mod = _mod_file(tmp)
        try:
            first = verify.verify_mod(mod, matrix_path=matrix)
        finally:
            server.stop()
        assert first["summary"]["row_outcomes"]["found"] == 4, first["summary"]
        # the server is gone now: a cached PE/PDB must still answer
        second = verify.verify_mod(mod, matrix_path=matrix)
        assert second["summary"]["row_outcomes"]["found"] == 4, second["summary"]
        stats = second["network"]["symbol_server"]["stats"]
        # the PE comes from the file cache and the PDB from the parsed-symbol
        # index cache, so nothing is downloaded at all the second time.
        assert stats["cache_hits"] >= 1 and stats["downloads"] == 0, stats
        assert os.path.isdir(cache), "downloads must be cached under the cache dir"
        index_files = [os.path.join(root, f)
                       for root, _dirs, files in os.walk(os.path.join(cache, "index"))
                       for f in files]
        assert index_files, "the parsed symbol index was not cached"
        assert second["modules"][0]["available"] is True, second["modules"][0]


def test_offline_never_claims_found_or_missing():
    with tempfile.TemporaryDirectory() as tmp:
        cache = os.path.join(tmp, "cache")
        matrix = _matrix_file(tmp, "http://127.0.0.1:1", cache)
        mod = _mod_file(tmp)
        section = verify.verify_mod(mod, matrix_path=matrix, offline=True)
        cells = [c for row in section["symbols"] for c in row["builds"].values()]
        assert cells, "no cells were produced"
        for cell in cells:
            assert cell["outcome"] == "unknown", cell
            assert cell["notes"], "an unknown verdict must say why"
        assert section["summary"]["row_outcomes"].get("found", 0) == 0
        assert section["summary"]["row_outcomes"].get("missing", 0) == 0
        assert section["network"]["offline"] is True
        assert section["network"]["symbol_server"]["stats"]["downloads"] == 0


def test_unreachable_server_is_unknown():
    with tempfile.TemporaryDirectory() as tmp:
        cache = os.path.join(tmp, "cache")
        matrix = _matrix_file(tmp, "http://127.0.0.1:9", cache)   # nothing listens
        mod = _mod_file(tmp)
        section = verify.verify_mod(mod, matrix_path=matrix)
        outcomes = {c["outcome"] for row in section["symbols"]
                    for c in row["builds"].values()}
        assert outcomes == {"unknown"}, outcomes
        module = section["modules"][0]
        assert module["available"] is False
        assert "PE not available" in (module.get("reason") or ""), module


def test_module_absent_on_a_build_is_unknown():
    """taskbar.dll does not exist on Windows 10: that is not a missing symbol."""
    with tempfile.TemporaryDirectory() as tmp:
        store = os.path.join(tmp, "store")
        os.makedirs(store)
        _publish(store)
        server = FakeSymbolServer(store)
        try:
            cache = os.path.join(tmp, "cache")
            builds = [{"id": "win11-24h2", "channel": "11-24H2"},
                      {"id": "win10-1507", "channel": "1507"}]
            pinned = {MODULE: {"11-24H2": {"timestamp": TIMESTAMP,
                                           "virtual_size": SIZE_OF_IMAGE,
                                           "version": "10.0.26100.1"}}}
            matrix = _matrix_file(tmp, server.base_url, cache, builds=builds,
                                  pinned=pinned)
            source = MOD_SOURCE.replace("// @include        twinui.pcshell.dll",
                                        "// @include        taskbar.dll") \
                               .replace('LoadLibrary(L"twinui.pcshell.dll")',
                                        'LoadLibrary(L"taskbar.dll")')
            mod = _mod_file(tmp, source)
            section = verify.verify_mod(mod, matrix_path=matrix)
            for row in section["symbols"]:
                cell = row["builds"]["win10-1507"]
                assert cell["outcome"] == "unknown", (row["symbol"], cell)
                assert any("not" in n or "unavailable" in n or "resolve" in n
                           for n in cell["notes"]), cell
        finally:
            server.stop()


def test_pe_without_a_debug_directory_is_unknown():
    with tempfile.TemporaryDirectory() as tmp:
        store = os.path.join(tmp, "store")
        os.makedirs(store)
        _publish(store, with_debug_directory=False)
        server = FakeSymbolServer(store)
        try:
            cache = os.path.join(tmp, "cache")
            matrix = _matrix_file(tmp, server.base_url, cache)
            section = verify.verify_mod(_mod_file(tmp), matrix_path=matrix)
            module = section["modules"][0]
            assert module["available"] is False, module
            assert "CodeView" in (module.get("reason") or ""), module
        finally:
            server.stop()


def test_local_module_skips_the_network():
    with tempfile.TemporaryDirectory() as tmp:
        pe_path = os.path.join(tmp, MODULE)
        with open(pe_path, "wb") as f:
            f.write(build_pe(pdb_name=PDB_NAME, guid=GUID))
        pdb_path = os.path.join(tmp, PDB_NAME)
        with open(pdb_path, "wb") as f:
            f.write(build_pdb(module=MODULE, guid=GUID))
        matrix_path = os.path.join(tmp, "matrix.yaml")
        with open(matrix_path, "w", encoding="utf-8") as f:
            f.write('schema: 1\n'
                    'symbol_server:\n  base_url: "http://127.0.0.1:1"\n'
                    '  cache_dir: "%s"\n'
                    'winbindex:\n  enabled: false\n'
                    'arch: "x86-64"\n'
                    'builds:\n  - id: "local"\n    label: "local files"\n'
                    '    channel: "11-24H2"\n    required: true\n'
                    'local_modules:\n  %s:\n    pe: "%s"\n    pdb: "%s"\n'
                    'limits:\n  sleep_between_downloads: 0\n'
                    % (os.path.join(tmp, "cache"), MODULE, pe_path, pdb_path))
        section = verify.verify_mod(_mod_file(tmp), matrix_path=matrix_path,
                                    offline=True)
        rows = _rows(section)
        cell = rows["?Invoke@CTaskFrame@@UEAAJXZ"]["builds"]["local"]
        assert cell["outcome"] == "found", cell
        assert section["modules"][0]["available"] is True
        assert section["network"]["symbol_server"]["stats"]["downloads"] == 0


def test_cli_exit_codes_and_json_output():
    with tempfile.TemporaryDirectory() as tmp:
        cache = os.path.join(tmp, "cache")
        matrix = _matrix_file(tmp, "http://127.0.0.1:1", cache)
        mod = _mod_file(tmp)
        out = os.path.join(tmp, "symbol-matrix.json")
        code = symbols_cli.main([mod, "--matrix", matrix, "--offline",
                                 "--json", out, "--quiet"])
        assert code == 3, "an all-unknown run must exit 3 (nothing was verified)"
        with open(out, encoding="utf-8") as f:
            document = json.load(f)
        assert document["schema"] == 1 and document["kind"] == "symbol_matrix"
        assert document["symbol_matrix"]["summary"]["row_outcomes"].get("found", 0) == 0
        assert "environment" in document

        listing = symbols_cli.main([mod, "--list"])
        assert listing == 0, "listing the extracted requests needs no network"
        assert symbols_cli.main([os.path.join(tmp, "nope.wh.cpp")]) == 2


def test_cli_reports_found_against_the_local_server(capsys=None):
    with tempfile.TemporaryDirectory() as tmp:
        store = os.path.join(tmp, "store")
        os.makedirs(store)
        _publish(store)
        server = FakeSymbolServer(store)
        try:
            cache = os.path.join(tmp, "cache")
            matrix = _matrix_file(tmp, server.base_url, cache)
            out = os.path.join(tmp, "section.json")
            code = symbols_cli.main([_mod_file(tmp), "--matrix", matrix,
                                     "--json", out, "--quiet"])
            assert code == 0, "a resolved matrix must exit 0, got %d" % code
            with open(out, encoding="utf-8") as f:
                section = json.load(f)["symbol_matrix"]
            assert section["summary"]["row_outcomes"]["found"] == 4
        finally:
            server.stop()


def test_report_attachment_is_additive():
    from awt import report
    rep = {"schema": 1,
           "mod": {"id": "m", "version": "1.0", "include": ["twinui.pcshell.dll"],
                   "architecture": "x86-64"},
           "build": {"ok": True, "errors": [], "warnings": []},
           "analysis": {"public_hooks": ["DrawTextW"], "internal_hooks": [],
                        "unknown_hooks": []},
           "include_analysis": [],
           "run": {"backend": "unavailable"},
           "environment": {},
           "shim": {"not_implemented_called": [], "simulated_with_limits": []},
           "verdict": {"level": "not_executed", "verified": [],
                       "not_verifiable_here": [], "requires_real_windows": False,
                       "summary": "nothing ran"},
           "artifacts": {"screenshots": [], "wine_log": None, "dumps": []}}
    before = json.dumps(rep, sort_keys=True)
    report.attach_symbol_matrix(rep, {"summary": {"symbols": 1, "builds": 2,
                                                  "row_outcomes": {"found": 1}},
                                      "disclaimer": "x"})
    assert "symbol_matrix" in rep
    assert rep["verdict"]["level"] == "not_executed", "the verdict must not change"
    text = report.summary(rep)
    assert "symbol matrix" in text, text
    # every original key is still there with the same value
    for key, value in json.loads(before).items():
        assert rep[key] == value or key == "verdict", key
    assert json.dumps(rep)                       # still serialisable
