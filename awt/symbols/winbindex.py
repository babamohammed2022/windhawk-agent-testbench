"""Resolve `module + Windows build` -> the PE signature a symbol server needs.

The Microsoft public symbol server only serves a file when you already know its
signature (`<TIMESTAMP:08X><SizeOfImage:x>` for the image, `<GUID><Age>` for the
PDB, which is read from the image's debug directory). It has no directory
listing. Two ways to get the first half:

1. **winbindex** (https://github.com/m417z/winbindex, `gh-pages` branch): a
   public index of Windows binaries keyed by SHA-256, with the Windows versions
   each file shipped in and its PE `timestamp` / `virtualSize`. Fetched through
   `api.github.com`, gzip'd JSON, cached in `.cache/winbindex/`.
2. **pinned signatures** in `configs/symbol-matrix.yaml`, for the modules and
   builds this bench cares about. They keep the tool usable when the index (or
   the network) is not reachable, and they make a run reproducible: the same
   build id always means the same binary.

If neither works the outcome for that cell is `unknown` ("module signature not
resolvable for this build") - never `missing`, because nothing was looked at.

Only public *metadata* (version strings, PE header fields) is stored in this
repository. No Microsoft binary or PDB is ever committed.
"""
import gzip
import json
import os
import time
import urllib.error
import urllib.request

from .. import env as host_env

WINBINDEX_REPO = "m417z/winbindex"
WINBINDEX_REF = "gh-pages"
WINBINDEX_PATH = "data/by_filename_compressed"
API_RAW = "https://api.github.com/repos/%s/contents/%s/%%s.json.gz?ref=%s" % (
    WINBINDEX_REPO, WINBINDEX_PATH, WINBINDEX_REF)

AMD64_MACHINE_TYPE = 34404
X86_MACHINE_TYPE = 332

# Channels used by winbindex, and the marketing names the matrix uses.
CHANNELS = {
    "win10-1507": "1507", "win10-1511": "1511", "win10-1607": "1607",
    "win10-1703": "1703", "win10-1709": "1709", "win10-1803": "1803",
    "win10-1809": "1809", "win10-1903": "1903", "win10-1909": "1909",
    "win10-2004": "2004", "win10-20h2": "20H2", "win10-21h1": "21H1",
    "win10-21h2": "21H2", "win10-22h2": "22H2",
    "win11-21h2": "11-21H2", "win11-22h2": "11-22H2",
    "win11-23h2": "11-23H2", "win11-24h2": "11-24H2",
    "win11-25h2": "11-25H2", "win11-26h1": "11-26H1",
}

# Signatures read out of winbindex on 2026-11-04 (amd64, newest cumulative
# update listed for each channel). Pinned so a run is reproducible and so the
# bench still works when api.github.com is unreachable. Refresh with
# `python3 -m awt.symbols --refresh-pinned`.
PINNED = {
    "twinui.pcshell.dll": {
        # not present before Windows 10 1607
        "22H2": {"timestamp": 0x36CDA8CA, "virtual_size": 0x6B4000,
                 "version": "10.0.19041.6328"},
        "11-21H2": {"timestamp": 0x4E107849, "virtual_size": 0x830000,
                    "version": "10.0.22000.3079"},
        "11-23H2": {"timestamp": 0x018CB3CD, "virtual_size": 0xA26000,
                    "version": "10.0.22621.6345"},
        "11-24H2": {"timestamp": 0xBC86BB4D, "virtual_size": 0x9A5000,
                    "version": "10.0.26100.9549"},
        "11-25H2": {"timestamp": 0x8E751D2B, "virtual_size": 0x9D2000,
                    "version": "10.0.26100.9818"},
    },
    "explorer.exe": {
        "1507": {"timestamp": 0x686382E5, "virtual_size": 0x44E000,
                 "version": "10.0.10240.21072"},
        "22H2": {"timestamp": 0x5506678D, "virtual_size": 0x5C9000,
                 "version": "10.0.19041.6392"},
        "11-21H2": {"timestamp": 0xF17E3E0F, "virtual_size": 0x4D3000,
                    "version": "10.0.22000.3079"},
        "11-23H2": {"timestamp": 0x754F843A, "virtual_size": 0x554000,
                    "version": "10.0.22621.7517"},
        "11-24H2": {"timestamp": 0xBEE7E13F, "virtual_size": 0x344000,
                    "version": "10.0.26100.9711"},
        "11-25H2": {"timestamp": 0xF9A8CE40, "virtual_size": 0x327000,
                    "version": "10.0.26100.9818"},
    },
    "taskbar.dll": {
        # Windows 11 only: the file does not exist on Windows 10
        "11-21H2": {"timestamp": 0x9523184F, "virtual_size": 0x33F000,
                    "version": "10.0.22000.3260"},
        "11-23H2": {"timestamp": 0x64EBA2DD, "virtual_size": 0x3B5000,
                    "version": "10.0.22621.6274"},
        "11-24H2": {"timestamp": 0x769B877B, "virtual_size": 0x308000,
                    "version": "10.0.26100.9711"},
        "11-25H2": {"timestamp": 0x617E422B, "virtual_size": 0x31A000,
                    "version": "10.0.26100.9818"},
    },
    "twinui.dll": {
        "1507": {"timestamp": 0x68E678A0, "virtual_size": 0xAD9000,
                 "version": "10.0.10240.21161"},
        "22H2": {"timestamp": 0xC534F761, "virtual_size": 0x618000,
                 "version": "10.0.19041.5915"},
        "11-21H2": {"timestamp": 0x3D4F3484, "virtual_size": 0x5AA000,
                    "version": "10.0.22000.3147"},
    },
}


def channel_for(build_id) -> str:
    """`win11-24h2` / `11-24H2` / `24H2` -> the winbindex channel key."""
    text = str(build_id or "").strip()
    if text in CHANNELS:
        return CHANNELS[text]
    values = set(CHANNELS.values())
    if text in values:
        return text
    upper = text.upper().replace("WIN11-", "11-").replace("WIN10-", "")
    if upper in values:
        return upper
    if not upper.startswith("11-") and ("11-" + upper) in values:
        return "11-" + upper
    return text


def signature(entry) -> str:
    return "%08X%x" % (int(entry["timestamp"]), int(entry["virtual_size"]))


class Resolver:
    """Pinned table first, live winbindex lookup second."""

    def __init__(self, cache_dir=None, offline=None, timeout=25.0, verbose=False,
                 pinned=None):
        self.cache_dir = cache_dir or host_env.cache_dir("winbindex")
        self.offline = host_env.offline() if offline is None else bool(offline)
        self.timeout = timeout
        self.verbose = verbose
        self.pinned = PINNED if pinned is None else pinned
        self.index_cache = {}
        self.notes = []

    # ---- live index --------------------------------------------------------
    def index_path(self, module) -> str:
        return os.path.join(self.cache_dir, module.lower() + ".json.gz")

    def fetch_index(self, module):
        """Return the winbindex payload for `module`, or (None, reason)."""
        module = module.lower()
        if module in self.index_cache:
            return self.index_cache[module], "memory cache"
        path = self.index_path(module)
        if os.path.isfile(path) and os.path.getsize(path):
            payload, note = self._load_index_file(path)
            if payload is not None:
                self.index_cache[module] = payload
                return payload, "cache %s" % os.path.relpath(path, self.cache_dir)
        if self.offline:
            return None, "offline mode: winbindex not queried for %s" % module
        url = API_RAW % module
        req = urllib.request.Request(url, headers={
            "User-Agent": "awt-symbol-client/1.0",
            "Accept": "application/vnd.github.raw"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = resp.read()
        except urllib.error.HTTPError as exc:
            reason = "winbindex HTTP %s for %s" % (exc.code, module)
            self.notes.append(reason)
            return None, reason
        except Exception as exc:
            reason = "winbindex fetch failed for %s: %s" % (module, exc)
            self.notes.append(reason)
            return None, reason
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        payload, note = self._load_index_file(path)
        if payload is None:
            return None, note
        self.index_cache[module] = payload
        return payload, "fetched %s (%d bytes)" % (url, len(data))

    @staticmethod
    def _load_index_file(path):
        try:
            with gzip.open(path, "rb") as f:
                return json.load(f), "gzip json"
        except OSError:
            pass
        except (ValueError, gzip.BadGzipFile) as exc:
            return None, "cached winbindex file %s is not readable json: %s" % (path, exc)
        try:
            with open(path, "rb") as f:
                return json.load(f), "plain json"
        except Exception as exc:
            return None, "cached winbindex file %s is not readable: %s" % (path, exc)

    def lookup_index(self, module, channel, arch="amd64"):
        """Pick one entry of the live index for a channel."""
        payload, note = self.fetch_index(module)
        if payload is None:
            return None, note
        machine = AMD64_MACHINE_TYPE if arch != "x86" else X86_MACHINE_TYPE
        best = None
        for _sha, entry in payload.items():
            info = entry.get("fileInfo") or {}
            if info.get("machineType") not in (None, machine):
                continue
            versions = entry.get("windowsVersions") or {}
            if channel not in versions:
                continue
            timestamp = info.get("timestamp")
            virtual = info.get("virtualSize") or info.get("size")
            if not timestamp or not virtual:
                continue
            cand = {"timestamp": int(timestamp), "virtual_size": int(virtual),
                    "version": info.get("version"), "sha256": info.get("sha256"),
                    "source": "winbindex", "channel": channel,
                    "updates": sorted((versions.get(channel) or {}).keys())}
            if best is None or _version_key(cand["version"]) > _version_key(best["version"]):
                best = cand
        if best is None:
            return None, ("%s has no %s amd64 entry in winbindex (the module may "
                          "not exist on that build)" % (module, channel))
        best["signature"] = signature(best)
        return best, note

    # ---- public ------------------------------------------------------------
    def resolve(self, module, build_id, arch="amd64", pinned_first=True):
        """{timestamp, virtual_size, signature, version, source} or (None, reason)."""
        if not module:
            return None, "request has no target module"
        module = os.path.basename(module.replace("\\", "/")).lower()
        channel = channel_for(build_id)
        if pinned_first:
            table = (self.pinned or {}).get(module) or {}
            entry = table.get(channel)
            if entry:
                out = dict(entry)
                out.update({"signature": signature(entry), "source": "pinned",
                            "channel": channel,
                            "pinned_note": "signature pinned from winbindex on "
                                           "2026-11-04; see configs/symbol-matrix.yaml"})
                return out, "pinned signature"
            if table and channel not in table:
                # a known module with no entry for this build is real information
                note = ("%s is not listed for %s in the pinned table (the file may "
                        "not exist on that build)" % (module, channel))
                if self.offline:
                    return None, note
                self.notes.append(note)
        entry, note = self.lookup_index(module, channel, arch)
        if entry is None:
            return None, note
        return entry, note

    def refresh_pinned(self, modules, builds):
        """Re-read the pinned values from the live index (writes a YAML block)."""
        out = {}
        for module in modules:
            rows = {}
            for build_id in builds:
                entry, note = self.lookup_index(module, channel_for(build_id))
                if entry:
                    rows[channel_for(build_id)] = {
                        "timestamp": entry["timestamp"],
                        "virtual_size": entry["virtual_size"],
                        "version": entry.get("version")}
                else:
                    self.notes.append("%s/%s: %s" % (module, build_id, note))
            if rows:
                out[module] = rows
        return out


def _version_key(version):
    """Sort `10.0.26100.9549` numerically; tolerate missing/garbage values."""
    if not version:
        return ()
    parts = []
    for chunk in str(version).split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def describe():
    return {"source": "winbindex (%s@%s)" % (WINBINDEX_REPO, WINBINDEX_REF),
            "api": API_RAW % "<module>",
            "pinned_modules": sorted(PINNED),
            "pinned_read_on": "2026-11-04",
            "channels": CHANNELS,
            "policy": ("only public metadata (version, PE timestamp/size) is stored "
                       "in this repository; Microsoft binaries and PDBs are cached "
                       "under .cache/ and never committed")}


def pinned_yaml(data=None) -> str:
    """Render a pinned table as YAML (used by --refresh-pinned)."""
    data = PINNED if data is None else data
    lines = ["pinned_signatures:"]
    for module in sorted(data):
        lines.append("  %s:" % module)
        for channel in sorted(data[module]):
            e = data[module][channel]
            lines.append("    %s: {timestamp: %d, virtual_size: %d, version: %s}"
                         % (channel, e["timestamp"], e["virtual_size"],
                            e.get("version") or "null"))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":                      # pragma: no cover - manual use
    import sys
    r = Resolver(verbose=True)
    for mod in sys.argv[1:] or ["twinui.pcshell.dll"]:
        for build in ("win10-22h2", "win11-24h2"):
            entry, note = r.resolve(mod, build)
            print(mod, build, entry, note)
        time.sleep(1)
