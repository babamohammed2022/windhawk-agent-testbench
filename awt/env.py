"""Environment probe: what this machine can actually do.

Every capability is *checked*, never assumed. Reports embed the probe result so a
reader can always tell whether a verdict came from real execution (Wine/Windows)
or from static analysis only.

Nothing here performs network I/O unless a caller explicitly asks for it
(`probe_network`), and `AWT_OFFLINE=1` disables those attempts entirely.
"""
import os
import platform
import shutil
import socket
import ssl
import sys
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SANDBOX = os.path.join(REPO, ".sandbox")
CACHE = os.environ.get("AWT_CACHE_DIR") or os.path.join(REPO, ".cache")

# hosts the features of this bench talk to; used by `--probe-network` and by the
# "why did this come back unknown" explanations.
KNOWN_HOSTS = {
    "microsoft_symbol_server": "https://msdl.microsoft.com/download/symbols/",
    "github_api": "https://api.github.com/",
    "browser7": "https://browser7.win/",
}

WINE_CANDIDATES = ("/usr/lib/wine/wine64", "/usr/lib/wine/wine", "/opt/wine/bin/wine64")


def offline() -> bool:
    """True when the user asked for no network access at all."""
    return os.environ.get("AWT_OFFLINE", "").strip().lower() in ("1", "true", "yes")


def which(name) -> str:
    """First executable from `name` (str or list) found on PATH, else None."""
    names = [name] if isinstance(name, str) else list(name)
    for n in names:
        if os.path.sep in n:
            if os.path.exists(n) and os.access(n, os.X_OK):
                return n
            continue
        found = shutil.which(n)
        if found:
            return found
    return None


def find_wine():
    """Return the Wine executable this bench would use (PATH first)."""
    override = os.environ.get("AWT_WINE")
    if override:
        return override if (os.path.sep not in override and shutil.which(override)) or \
            os.access(override, os.X_OK) else None
    found = which(["wine64", "wine"])
    if found:
        return found
    for cand in WINE_CANDIDATES:
        if os.access(cand, os.X_OK):
            return cand
    return None


def python_package(name) -> str:
    """Version string of an importable python package, else 'missing'."""
    try:
        mod = __import__(name)
    except Exception:
        return "missing"
    return getattr(mod, "__version__", "present")


def probe_tools() -> dict:
    """External binaries, checked with `which` (no execution)."""
    return {
        "wine": find_wine(),
        "mingw_cxx": which(["x86_64-w64-mingw32-g++", "x86_64-w64-mingw32-clang++"]),
        "mingw_nm": which("x86_64-w64-mingw32-nm"),
        "xvfb": which("Xvfb"),
        "imagemagick": which(["import", "convert"]),
        "llvm_pdbutil": which("llvm-pdbutil"),
        "llvm_undname": which("llvm-undname"),
        "cabextract": which(["cabextract", "expand"]),
        "qemu_img": which("qemu-img"),
        "qemu_system": which(["qemu-system-x86_64", "qemu-system-i386"]),
        "vboxmanage": which(["VBoxManage", "vboxmanage"]),
        "virsh": which("virsh"),
        "powershell": which(["pwsh", "powershell"]),
    }


def probe_packages() -> dict:
    return {
        "yaml": python_package("yaml"),
        "numpy": python_package("numpy"),
        "PIL": python_package("PIL"),
        "playwright": python_package("playwright"),
    }


def probe_network(hosts=None, timeout=8.0) -> dict:
    """HTTP HEAD/GET probe of the hosts this bench uses.

    Result per host: reachable / unreachable / skipped(offline) plus the reason.
    A probe failure is never fatal: features degrade to 'unknown'/'unavailable'.
    """
    hosts = hosts or KNOWN_HOSTS
    out = {}
    for key, url in hosts.items():
        if offline():
            out[key] = {"url": url, "reachable": False, "reason": "AWT_OFFLINE=1"}
            continue
        try:
            req = urllib.request.Request(url, method="GET",
                                         headers={"User-Agent": "awt-probe/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                out[key] = {"url": url, "reachable": True, "http_status": resp.status}
        except urllib.error.HTTPError as exc:
            # An HTTP error still proves the host answered (symbol servers answer
            # 403/404 to a bare directory request).
            out[key] = {"url": url, "reachable": True, "http_status": exc.code,
                        "reason": "HTTP %d" % exc.code}
        except Exception as exc:  # URLError, ssl errors, timeouts, DNS
            out[key] = {"url": url, "reachable": False,
                        "reason": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    return out


def probe(with_network=False) -> dict:
    """Full environment description, safe to embed in a report."""
    env = {
        "schema": 1,
        "host_os": "%s %s" % (platform.system(), platform.release()),
        "python": sys.version.split()[0],
        "repo": REPO,
        "cache_dir": CACHE,
        "offline_requested": offline(),
        "tools": probe_tools(),
        "python_packages": probe_packages(),
    }
    if with_network:
        env["network"] = probe_network()
    return env


def toolchain_ready(env=None) -> tuple:
    """(ready, reason) for the Wine execution path (build + run)."""
    env = env or probe()
    tools = env["tools"]
    missing = [name for name, key in (("wine", "wine"), ("MinGW C++ compiler", "mingw_cxx"),
                                      ("Xvfb", "xvfb")) if not tools.get(key)]
    if missing:
        return False, ("toolchain missing: %s (no Wine execution possible in this "
                       "environment; run ./setup.sh on a machine with package access)"
                       % ", ".join(missing))
    return True, "ok"


def sandbox_dir(*parts) -> str:
    path = os.path.join(SANDBOX, *parts) if parts else SANDBOX
    os.makedirs(path, exist_ok=True)
    return path


def cache_dir(*parts) -> str:
    path = os.path.join(CACHE, *parts) if parts else CACHE
    os.makedirs(path, exist_ok=True)
    return path


def port_is_open(host, port, timeout=1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def tls_available() -> bool:
    return hasattr(ssl, "create_default_context")
