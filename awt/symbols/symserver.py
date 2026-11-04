"""Client for a Microsoft-style public symbol server, with a local cache.

Layout (verified against `https://msdl.microsoft.com/download/symbols`):

    <base>/<image name>/<TIMESTAMP:08X><SizeOfImage:x>/<image name>       (PE)
    <base>/<pdb file name>/<GUID32><Age:x>/<pdb file name>                (PDB)

The server answers 200 and redirects to an Azure blob URL with a short-lived
`sp=r` signature; directory listings are refused, so a symbol can only be
fetched when its signature is already known (from winbindex or from the PE's
own debug directory).

Hard rules this module follows:

* downloaded files land in `.cache/symbols/` (git-ignored) and are **never**
  committed - they are Microsoft binaries;
* a failure is a failure: `get()` returns `(None, reason)` and the caller
  reports `unknown`. Nothing here is allowed to turn an unreachable server into
  a "symbol is missing" claim;
* `AWT_OFFLINE=1`, `--offline` or an unreachable host short-circuit the network
  instead of hanging (default timeout 20 s per request, 2 attempts).
"""
import gzip
import os
import shutil
import time
import urllib.error
import urllib.request

from .. import env as host_env

DEFAULT_BASE = "https://msdl.microsoft.com/download/symbols"
USER_AGENT = "awt-symbol-client/1.0 (linux; research use; single-file fetches)"
TIMEOUT = 20.0
ATTEMPTS = 2


class SymbolServerError(Exception):
    pass


def _cache_root(cache_dir=None) -> str:
    return cache_dir or host_env.cache_dir("symbols")


class SymbolServer:
    """Fetch-by-signature client with an on-disk cache."""

    def __init__(self, base_url=None, cache_dir=None, offline=None,
                 timeout=TIMEOUT, attempts=ATTEMPTS, verbose=False):
        self.base_url = (base_url or os.environ.get("AWT_SYMBOL_SERVER")
                         or DEFAULT_BASE).rstrip("/")
        self.cache_dir = _cache_root(cache_dir)
        self.offline = host_env.offline() if offline is None else bool(offline)
        self.timeout = timeout
        self.attempts = max(1, attempts)
        self.verbose = verbose
        self.stats = {"cache_hits": 0, "downloads": 0, "bytes": 0,
                      "failures": 0, "offline_shortcut": 0}
        self.errors = []

    # ---- paths -------------------------------------------------------------
    def cache_path(self, filename, signature, suffix="") -> str:
        safe_sig = "".join(c for c in signature if c.isalnum() or c in "-_.")
        return os.path.join(self.cache_dir, filename, safe_sig,
                            filename + suffix)

    def cached(self, filename, signature, suffix=""):
        path = self.cache_path(filename, signature, suffix)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
        return None

    # ---- urls --------------------------------------------------------------
    @staticmethod
    def pe_url_path(name, timestamp, size_of_image) -> str:
        return "%s/%08X%x/%s" % (name, int(timestamp), int(size_of_image), name)

    @staticmethod
    def pdb_url_path(pdb_name, guid_hex, age) -> str:
        return "%s/%s%X/%s" % (pdb_name, guid_hex.upper().replace("-", ""),
                               int(age), pdb_name)

    def url(self, relative) -> str:
        return "%s/%s" % (self.base_url, relative.lstrip("/"))

    # ---- fetch -------------------------------------------------------------
    def get(self, filename, signature, kind="pe", expect_magic=None):
        """Return (path, note). `path` is None when the file could not be had.

        `signature` is the symbol-server key: `<TIMESTAMP:08X><SizeOfImage:x>`
        for a PE, `<GUID32><Age>` for a PDB.
        """
        if not filename or not signature:
            return None, "no filename/signature to request"
        if isinstance(signature, (tuple, list)):
            # (timestamp, size_of_image) -> the documented symbol-store key
            signature = "%08X%x" % (int(signature[0]), int(signature[1]))
        signature = str(signature)
        hit = self.cached(filename, signature)
        if hit:
            self.stats["cache_hits"] += 1
            return hit, "cache hit %s" % os.path.relpath(hit, self.cache_dir)
        if self.offline:
            self.stats["offline_shortcut"] += 1
            return None, "offline mode: %s/%s not requested" % (filename, signature)

        relative = self.pe_url_path(filename, *self._split_pe(signature)) \
            if kind == "pe" else filename + "/" + signature + "/" + filename
        data, note = self._download(relative)
        if data is None:
            # symsrv also stores compressed payloads next to the plain name
            alt, alt_note = self._download(relative[:-len(filename)] +
                                           _compressed_name(filename))
            if alt is None:
                return None, note
            if not alt.startswith(b"MSCF"):
                data, note = alt, alt_note
            else:
                self.stats["failures"] += 1
                return None, ("server returned a compressed symbol file (%s) and "
                              "no cab extractor is available on this host"
                              % _compressed_name(filename))
        if expect_magic and not data.startswith(expect_magic):
            self.stats["failures"] += 1
            return None, ("downloaded %s but it does not look like a %s "
                          "(first bytes %r)" % (filename, kind, data[:8]))
        path = self.cache_path(filename, signature)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        shutil.move(tmp, path)
        self.stats["downloads"] += 1
        self.stats["bytes"] += len(data)
        if self.verbose:
            print("[symbols] fetched %s (%d bytes)" % (relative, len(data)))
        return path, note or "downloaded from %s" % self.base_url

    @staticmethod
    def _split_pe(signature):
        """`BC86BB4D9A5000` -> (0xBC86BB4D, 0x9A5000).

        The SizeOfImage field is written with a lowercase `%x` and has no fixed
        width, so the split is by position from the right only when the caller
        supplied a tuple; a plain hex string is decoded by trying the documented
        8-char timestamp prefix.
        """
        if isinstance(signature, (tuple, list)):
            return signature
        text = str(signature)
        if len(text) <= 8:
            raise SymbolServerError("PE signature %r is too short" % text)
        return int(text[:8], 16), int(text[8:], 16)

    def _download(self, relative):
        url = self.url(relative)
        last = "not attempted"
        for attempt in range(self.attempts):
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": USER_AGENT, "Accept": "*/*"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = resp.read()
                if not data:
                    last = "empty response from %s" % url
                    continue
                return data, "downloaded %s (attempt %d)" % (relative, attempt + 1)
            except urllib.error.HTTPError as exc:
                last = "HTTP %s for %s" % (exc.code, url)
                if exc.code in (404, 403, 410):
                    break                      # the signature is simply not there
            except urllib.error.URLError as exc:
                last = "network error for %s: %s" % (url, getattr(exc, "reason", exc))
            except Exception as exc:           # pragma: no cover - defensive
                last = "%s: %s" % (type(exc).__name__, exc)
            time.sleep(0.5 * (attempt + 1))
        self.stats["failures"] += 1
        self.errors.append(last)
        return None, last

    # ---- diagnostics -------------------------------------------------------
    def probe(self):
        """Reachability of the symbol server, reported verbatim in the JSON."""
        if self.offline:
            return {"base_url": self.base_url, "reachable": False,
                    "detail": "offline mode (AWT_OFFLINE or --offline)",
                    "checked": False}
        started = time.time()
        _data, note = self._download("")       # the index itself: expect 404/403
        return {
            "base_url": self.base_url,
            "reachable": ("HTTP 404" in note or "HTTP 403" in note
                          or "HTTP 400" in note or "downloaded" in note),
            "detail": note,
            "checked": True,
            "seconds": round(time.time() - started, 2),
        }

    def describe(self):
        return {"base_url": self.base_url, "cache_dir": self.cache_dir,
                "offline": self.offline, "timeout": self.timeout,
                "stats": dict(self.stats), "errors": list(self.errors)[:10]}


def _compressed_name(filename):
    root, _ext = os.path.splitext(filename)
    return root + "_"


def gunzip_if_needed(path):
    """winbindex payloads are gzip'd; a cache file may be either."""
    with open(path, "rb") as f:
        head = f.read(2)
    if head != b"\x1f\x8b":
        return path
    out = path[:-3] if path.endswith(".gz") else path + ".json"
    if os.path.isfile(out):
        return out
    with gzip.open(path, "rb") as src, open(out, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return out
