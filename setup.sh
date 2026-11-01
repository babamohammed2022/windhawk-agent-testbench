#!/bin/bash
# automatic-windhawk-testing — environment setup.
# Idempotent: detects what is already installed and skips it. Multi-source fallbacks:
#   1) system package manager (apt-get)   2) GitHub release tarballs   3) clear error + exit 41/42/43
# Never touches anything outside this repo directory (Wine prefix lives in .sandbox/prefix).
set -u
REPO="$(cd "$(dirname "$0")" && pwd)"
SB="$REPO/.sandbox"
mkdir -p "$SB"

log()  { printf '[setup] %s\n' "$*"; }
fail() { printf '[setup] ERROR: %s\n' "$*" >&2; exit "${2:-40}"; }

# ---------------------------------------------------------------- components
HAVE_APT=0
if command -v apt-get >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then HAVE_APT=1; fi

apt_install() {  # apt_install <packages...>
    [ "$HAVE_APT" = 1 ] || return 1
    sudo apt-get install -y --no-install-recommends "$@" >/dev/null 2>&1
}

# Wine (64-bit). Some sandboxes ship it at /usr/lib/wine/wine64 without a PATH entry.
if command -v wine64 >/dev/null 2>&1 || command -v wine >/dev/null 2>&1 || [ -x /usr/lib/wine/wine64 ]; then
    log "wine: already present ($([ -x /usr/lib/wine/wine64 ] && echo /usr/lib/wine/wine64 || command -v wine64 wine))"
else
    log "wine: installing via apt-get ..."
    apt_install wine64 || fail "cannot install wine64; attach a Wine build (e.g. Kron4ek/Wine-Builds) to $SB/wine.tar.xz" 41
fi
WINE_BIN="$(command -v wine64 || command -v wine || echo /usr/lib/wine/wine64)"

# MinGW toolchain (x86-64). llvm-mingw preferred per docs, gcc-mingw accepted.
if command -v x86_64-w64-mingw32-g++ >/dev/null 2>&1 || command -v x86_64-w64-mingw32-clang++ >/dev/null 2>&1; then
    log "mingw: already present"
else
    log "mingw: installing g++-mingw-w64-x86-64 via apt-get ..."
    apt_install g++-mingw-w64-x86-64 || fail "cannot install mingw; attach llvm-mingw tarball to $SB/llvm-mingw.tar.xz" 42
fi

# Xvfb + screenshot helpers (optional but recommended).
command -v Xvfb >/dev/null 2>&1 || { log "Xvfb: installing ..."; apt_install xvfb; }
command -v convert >/dev/null 2>&1 || command -v import >/dev/null 2>&1 || { log "imagemagick: installing ..."; apt_install imagemagick; }
command -v Xvfb >/dev/null 2>&1 || fail "Xvfb unavailable; headless runs impossible" 43
log "xvfb/imagemagick: ok"

# ---------------------------------------------------------------- MinHook (vendored)
MH="$REPO/third_party/minhook"
if [ -f "$MH/src/hook.c" ]; then
    log "minhook: vendored sources present"
else
    log "minhook: fetching from GitHub ..."
    mkdir -p "$REPO/third_party"
    if curl -sSL -m 60 -o "$SB/minhook.tar.gz" https://github.com/TsudaKageyu/minhook/archive/refs/tags/v1.3.3.tar.gz; then
        tar -xzf "$SB/minhook.tar.gz" -C "$REPO/third_party"
        mv "$REPO/third_party/minhook-1.3.3" "$MH"
        log "minhook: vendored at third_party/minhook"
    else
        fail "GitHub unreachable and MinHook not vendored; put minhook sources in $MH" 44
    fi
fi

# ---------------------------------------------------------------- official headers cache
OFF="$REPO/docs/official"
if [ -f "$OFF/windhawk_api.h" ] && [ -f "$OFF/windhawk_utils.h" ]; then
    log "official headers: cached in docs/official"
else
    log "official headers: fetching from GitHub (ramensoftware/windhawk-mods .vscode/windhawk_headers_1.7.3) ..."
    mkdir -p "$OFF"
    base="https://raw.githubusercontent.com/ramensoftware/windhawk-mods/HEAD/.vscode/windhawk_headers_1.7.3"
    curl -sSf -m 30 -o "$OFF/windhawk_api.h"   "$base/windhawk_api.h"   || log "WARN: windhawk_api.h fetch failed"
    curl -sSf -m 30 -o "$OFF/windhawk_utils.h" "$base/windhawk_utils.h" || log "WARN: windhawk_utils.h fetch failed"
    curl -sSf -m 30 -o "$OFF/mods_api.h" "https://raw.githubusercontent.com/ramensoftware/windhawk/HEAD/src/windhawk/engine/mods_api.h" || true
    curl -sSf -m 30 -o "$OFF/mods_api_internal.h" "https://raw.githubusercontent.com/ramensoftware/windhawk/HEAD/src/windhawk/engine/mods_api_internal.h" || true
fi

# ---------------------------------------------------------------- self-check
# never let a crash spawn winedbg (it hangs headless runs)
if [ -d "$SB/prefix" ]; then
    WINEPREFIX="$SB/prefix" WINEDEBUG=-all "$WINE_BIN" reg add "HKCU\\Software\\Wine\\Debug" /v CrashHandler /d "" /f >/dev/null 2>&1 || true
fi

log "self-check: toolchain versions"
"$WINE_BIN" --version 2>/dev/null | sed 's/^/  wine: /' || log "  wine: version query failed"
x86_64-w64-mingw32-g++ --version 2>/dev/null | head -1 | sed 's/^/  mingw: /'
python3 --version 2>/dev/null | sed 's/^/  python: /'

log "self-check: trivial Win32 exe builds and runs headless"
T="$SB/m1check"; mkdir -p "$T"
cat > "$T/hello.c" <<'EOF'
#include <windows.h>
#include <stdio.h>
int main(void) { printf("M1-HELLO from Wine\n"); fflush(stdout); return 0; }
EOF
if x86_64-w64-mingw32-gcc -O1 -o "$T/hello.exe" "$T/hello.c" 2>"$T/err.txt"; then
    pkill Xvfb 2>/dev/null; sleep 0.5
    Xvfb :93 -screen 0 800x600x24 >/dev/null 2>&1 &
    XPID=$!; sleep 1.5
    OUT=$(DISPLAY=:93 WINEPREFIX="$SB/prefix" WINEDEBUG=-all timeout 90 "$WINE_BIN" "$T/hello.exe" 2>/dev/null)
    kill $XPID 2>/dev/null
    case "$OUT" in
        *M1-HELLO*) log "self-check: PASS ($OUT)";;
        *) fail "trivial exe did not run under Wine (output: '$OUT')" 45;;
    esac
else
    fail "trivial exe failed to compile: $(head -3 "$T/err.txt")" 46
fi

log "setup complete."
exit 0
