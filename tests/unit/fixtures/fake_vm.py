"""A fake hypervisor and a fake in-guest agent.

The self-hosted VM backend talks to a machine this repository cannot provide, so
its protocol is exercised against this script instead: it plays both sides of the
shared folder (heartbeat, result, logs, screenshot) and can be told to misbehave
in exactly the ways a real VM misbehaves - never booting, booting but never
finishing the job, or writing a truncated screenshot.

Used from a VM configuration like:

    commands:
      start: 'python3 {repo}/tests/unit/fixtures/fake_vm.py --share {shared}
              --behaviour ok --detach'

`--detach` re-spawns the script in the background and exits immediately, the way
`qemu-system-x86_64 -daemonize` does, so the backend's "start returns promptly,
then wait for the heartbeat" sequence is the one being tested.
"""
import argparse
import json
import os
import struct
import subprocess
import sys
import time
import zlib

PROTOCOL = 1
AGENT = "fake-awt-vm-agent/1"

BEHAVIOURS = ("ok", "silent", "no-result", "bad-screenshot", "crash", "server-guest")

HOST_OUTPUT = """awt-host starting
[awt-host] loading mod
[awt-host] init returned 1
[awt-host] hooks applied count 2
[awt-host] target exit code 0
awt-host done
"""

MOD_LOG = """autoinit returned 1
hooks applied 2
DrawTextW: called (hook fired)
DrawTextW: called (hook fired)
MessageBoxW: called
"""

STUB_LOG = "Wh_FindFirstSymbol\n"


def write_png(path, width=8, height=6, rgb=(40, 90, 160), corrupt=False):
    """A real (if tiny) PNG, written with zlib+struct: no Pillow needed."""
    if corrupt:
        with open(path, "wb") as f:
            f.write(b"not a png")          # 9 bytes: too small and no signature
        return path

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
    return path


def _atomic_json(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def guest_environment(behaviour):
    """What a real agent would report about its own Windows."""
    server = behaviour == "server-guest"
    return {
        "backend": "self-hosted-vm",
        "os": "Windows",
        "os_version": "10.0.20348.1" if server else "10.0.22000.3079",
        "edition": ("Windows Server 2022" if server
                    else "Windows 11 Pro 21H2"),
        "build_number": 20348 if server else 22000,
        "product_type": "server" if server else "client",
        "shell_profile": "server" if server else "client",
        "virtualization": "fake-hypervisor",
        "vm_used": True,
        "arch": "AMD64",
        "hostname": "FAKE-GUEST",
        "collected_by": AGENT,
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seconds": None,
        "notes": [],
    }


def play_guest(share, behaviour, poll=0.2, wait=30.0):
    """Behave like `vm/agent/awt_agent.ps1` inside the VM."""
    outbox = os.path.join(share, "outbox")
    os.makedirs(os.path.join(outbox, "logs"), exist_ok=True)
    if behaviour == "silent":
        # the VM never reaches a usable desktop: nothing is ever written
        time.sleep(wait)
        return 0
    environment = guest_environment(behaviour)
    _atomic_json(os.path.join(outbox, "heartbeat.json"), {
        "protocol": PROTOCOL, "agent_version": AGENT, "share": share,
        "heartbeat_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": environment,
    })
    if behaviour == "no-result":
        time.sleep(wait)          # alive, but the job never completes
        return 0

    job_path = os.path.join(share, "job.json")
    deadline = time.time() + wait
    job = None
    while time.time() < deadline:
        job = _read_json(job_path)
        if job:
            break
        time.sleep(poll)
    if not job:
        _atomic_json(os.path.join(outbox, "result.json"), {
            "protocol": PROTOCOL, "agent": AGENT, "status": "not_executed",
            "reason": "no job.json appeared in the shared folder",
            "environment": environment,
        })
        return 0

    crashed = behaviour == "crash"
    host_output = ("awt-host starting\n[awt-host] init returned 1\n"
                   "unhandled page fault at 0x00000000, Aborting\n") if crashed \
        else HOST_OUTPUT
    with open(os.path.join(outbox, "logs", "host-output.log"), "w",
              encoding="utf-8") as f:
        f.write(host_output)
    with open(os.path.join(outbox, "logs", "mod.log"), "w", encoding="utf-8") as f:
        f.write(MOD_LOG)
    with open(os.path.join(outbox, "logs", "stub.log"), "w", encoding="utf-8") as f:
        f.write(STUB_LOG)

    screenshot = None
    if job.get("screenshot"):
        path = os.path.join(outbox, "screenshot.png")
        write_png(path, corrupt=(behaviour == "bad-screenshot"))
        screenshot = "screenshot.png"

    _atomic_json(os.path.join(outbox, "result.json"), {
        "protocol": PROTOCOL,
        "agent": AGENT,
        "job_id": job.get("job_id"),
        "status": "completed",
        "reason": None,
        "environment": environment,
        "build": {"ok": True, "errors": [], "warnings": []},
        "run": {
            "backend": "self-hosted-vm",
            "target": job.get("target"),
            "mode": job.get("mode"),
            "host_output": host_output,
            "mod_log": "" if crashed else MOD_LOG,
            "stub_log": STUB_LOG,
            "exit_code": -1073741819 if crashed else 0,
            "timed_out": False,
            "hooks_fired": {} if crashed else {"DrawTextW": 2, "MessageBoxW": 1},
        },
        "artifacts": {"screenshot": screenshot,
                      "logs": ["host-output.log", "mod.log", "stub.log"]},
        "verdict": {"guest_claim": "the guest agent makes no verdict of its own"},
    })
    return 0


def mark(text, path):
    """Stand in for `virsh snapshot-revert` / `virsh destroy`: leave a trace."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(text + "\n")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--share", help="the shared folder")
    parser.add_argument("--mark", help="append this line to --mark-file and exit")
    parser.add_argument("--mark-file", help="where --mark writes")
    parser.add_argument("--behaviour", default="ok", choices=BEHAVIOURS)
    parser.add_argument("--detach", action="store_true",
                        help="re-spawn in the background and exit (like -daemonize)")
    parser.add_argument("--poll", type=float, default=0.2)
    parser.add_argument("--wait", type=float, default=30.0)
    args = parser.parse_args(argv)

    if args.mark:
        if not args.mark_file:
            parser.error("--mark needs --mark-file")
        return mark(args.mark, args.mark_file)
    if not args.share:
        parser.error("--share is required unless --mark is used")
    if args.detach:
        child = [sys.executable, os.path.abspath(__file__), "--share", args.share,
                 "--behaviour", args.behaviour, "--poll", str(args.poll),
                 "--wait", str(args.wait)]
        log = os.path.join(args.share, "outbox", "fake-vm.log")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "ab") as handle:
            subprocess.Popen(child, stdout=handle, stderr=handle,
                             start_new_session=True)
        return 0
    return play_guest(args.share, args.behaviour, poll=args.poll, wait=args.wait)


if __name__ == "__main__":
    sys.exit(main())
