"""
detect.py — Locate a DC34 (Baosec) badge.

Resolution order: --port, DC34_IMAGE_PORT, USB VID/PID, descriptor name, active probe.

Device facts (confirmed on hardware): 1d50:6198, "Baochip" / "Baosec-lite".
Console echoes each command as "[console] <cmd>\r\n" before responding;
`ver` -> "ver [xous]"; unknown commands -> "Commands: echo, ver, test, image, bio".
"""

from __future__ import annotations

import os
import sys
import time
from typing import NamedTuple, Optional

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial is required: pip install pyserial")

# -- Identity constants --------------------------------------------------------

# 0x1d50 is OpenMoko's shared community VID, so the pair must match, never the VID alone.
KNOWN_USB_IDS = {(0x1D50, 0x6198)}

# The hardware advertises no "dc34"/"defcon" string anywhere.
NAME_HINTS    = ("baosec", "baochip")

VER_TOKEN     = "xous"
HELP_TOKEN    = "Commands:"
CONSOLE_ECHO  = "[console]"

BAUD_RATE     = 1_000_000
PROBE_TIMEOUT = 0.75   # measured round-trip is ~18 ms mean, 60 ms worst

ENV_PORT_VAR  = "DC34_IMAGE_PORT"

SCORE_USB_ID  = 100
SCORE_NAME    = 50
SCORE_PROBED  = 25


class Candidate(NamedTuple):
    device: str
    score: int
    label: str
    probed: bool


class DetectError(Exception):
    """Raised when a port cannot be resolved or opened."""


# -- Permission diagnostics ----------------------------------------------------

def permission_hint(device: str) -> str:
    """Explain EACCES on `device`, reading the owning group off the node."""
    try:
        import grp
        st    = os.stat(device)
        group = grp.getgrgid(st.st_gid).gr_name
    except Exception:
        return (f"Permission denied opening {device}.\n"
                f"        You likely need to join the group that owns the port.")

    lines = [f"Permission denied opening {device}.",
             f"        The port is owned by group '{group}'."]

    # Whether this process carries the group is a different question from whether
    # usermod has added it; confusing the two sends people in circles.
    try:
        active = {grp.getgrgid(g).gr_name for g in os.getgroups()}
    except Exception:
        active = set()

    try:
        import getpass
        entry   = grp.getgrnam(group)
        on_disk = getpass.getuser() in entry.gr_mem or entry.gr_gid == os.getgid()
    except Exception:
        on_disk = False

    if group in active:
        lines.append(f"        This process has '{group}', so check the device node still exists.")
    elif on_disk:
        lines.append(f"        You ARE a member of '{group}' in /etc/group, but this process does")
        lines.append(f"        not carry it — group membership is fixed at login.")
        lines.append(f"        Fix: log out and back in (restarting the terminal is not enough).")
        lines.append(f"        To test without logging out:  newgrp {group}")
    else:
        lines.append(f"        Fix: sudo usermod -aG {group} $USER, then log out and back in.")
    return "\n".join(lines)


def open_port(device: str, timeout: float = 4.0) -> serial.Serial:
    try:
        return serial.Serial(
            device,
            baudrate = BAUD_RATE,
            bytesize = serial.EIGHTBITS,
            parity   = serial.PARITY_NONE,
            stopbits = serial.STOPBITS_ONE,
            timeout  = timeout,
        )
    except serial.SerialException as e:
        if getattr(e, "errno", None) == 13 or "Permission denied" in str(e):
            raise DetectError(permission_hint(device)) from e
        raise DetectError(f"Cannot open serial port {device}: {e}") from e


# -- Active probe --------------------------------------------------------------

def _drain(ser: serial.Serial, quiet_for: float = 0.15, limit: float = PROBE_TIMEOUT) -> str:
    """Read until the device is silent for `quiet_for`, or `limit` elapses."""
    chunks: list[bytes] = []
    start = last = time.monotonic()
    while time.monotonic() - start < limit:
        n = ser.in_waiting
        if n:
            chunks.append(ser.read(n))
            last = time.monotonic()
        elif time.monotonic() - last >= quiet_for:
            break
        else:
            time.sleep(0.01)
    return b"".join(chunks).decode("ascii", errors="replace")


def probe(device: str) -> bool:
    """
    Return True if `device` speaks the badge console protocol.

    Non-destructive: never sends a command that alters the display.
    """
    nonce = f"dc34probe{os.getpid():06d}"
    try:
        ser = open_port(device, timeout=PROBE_TIMEOUT)
    except DetectError:
        return False

    try:
        time.sleep(0.05)
        ser.reset_input_buffer()

        ser.write(f"echo {nonce}\n".encode("ascii"))
        for line in _drain(ser).splitlines():
            line = line.strip()
            # The command echo contains the nonce too; matching it would make any
            # device that echoes input look like a badge.
            if not line.startswith(CONSOLE_ECHO) and nonce in line:
                return True

        ser.reset_input_buffer()
        ser.write(b"ver\n")
        reply = _drain(ser)
        return VER_TOKEN in reply.lower() or HELP_TOKEN in reply
    except (serial.SerialException, OSError):
        return False
    finally:
        try:
            ser.close()
        except Exception:
            pass


# -- Enumeration ---------------------------------------------------------------

def _describe(p) -> str:
    ids  = f"{p.vid:04x}:{p.pid:04x}" if p.vid is not None and p.pid is not None else "????:????"
    name = " ".join(x for x in (p.manufacturer, p.product) if x) or "unknown device"
    return f"{name} ({ids})"


def enumerate_candidates(do_probe: bool = True) -> list[Candidate]:
    """Score every USB serial port, highest first."""
    found: list[Candidate] = []
    unscored = []

    for p in list_ports.comports():
        # Legacy 16550 ports have no VID; this discards ~32 /dev/ttyS* on a typical laptop.
        if p.vid is None:
            continue

        label = _describe(p)
        if (p.vid, p.pid) in KNOWN_USB_IDS:
            found.append(Candidate(p.device, SCORE_USB_ID, label, False))
            continue

        blob = f"{p.manufacturer or ''} {p.product or ''}".lower()
        if any(h in blob for h in NAME_HINTS):
            found.append(Candidate(p.device, SCORE_NAME, label, False))
            continue

        unscored.append((p.device, label))

    if do_probe and not found:
        for device, label in unscored:
            if probe(device):
                found.append(Candidate(device, SCORE_PROBED, label, True))

    return sorted(found, key=lambda c: -c.score)


def all_ports_table() -> str:
    rows = []
    for p in list_ports.comports():
        if p.vid is None:
            continue
        mark = " <- badge" if (p.vid, p.pid) in KNOWN_USB_IDS else ""
        rows.append(f"  {p.device:<20} {_describe(p)}{mark}")
    if not rows:
        return "  (no USB serial devices found -- only legacy /dev/ttyS* ports, if any)"
    return "\n".join(rows)


# -- Resolution ----------------------------------------------------------------

def resolve_port(explicit: Optional[str] = None,
                 wait: float = 0.0,
                 interactive: Optional[bool] = None) -> str:
    """Return the port to use, or raise DetectError with an actionable message."""
    if explicit:
        return explicit

    env = os.environ.get(ENV_PORT_VAR)
    if env:
        print(f"[INFO] Using {ENV_PORT_VAR}={env}")
        return env

    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()

    deadline  = time.monotonic() + wait
    announced = False
    while True:
        found = enumerate_candidates()
        if found:
            break
        if time.monotonic() >= deadline:
            raise DetectError(
                "No DC34 badge found.\n\n"
                "Serial devices seen:\n"
                f"{all_ports_table()}\n\n"
                "  - Is the badge plugged in and powered on?\n"
                "  - Try --wait 30 and plug it in when prompted.\n"
                "  - If it is listed above but not detected, pass --port explicitly\n"
                "    and please report the VID:PID so it can be added."
            )
        if not announced:
            print("[INFO] Waiting for a badge to be plugged in...")
            announced = True
        time.sleep(0.4)

    if len(found) == 1:
        c = found[0]
        via = "probe" if c.probed else "USB id"
        print(f"[INFO] Auto-detected badge on {c.device} -- {c.label} (via {via})")
        return c.device

    top = [c for c in found if c.score == found[0].score]
    if len(top) == 1:
        print(f"[INFO] Auto-detected badge on {top[0].device} -- {top[0].label}")
        return top[0].device

    if not interactive:
        listing = "\n".join(f"  {c.device:<20} {c.label}" for c in top)
        raise DetectError(f"Multiple badges found; pass --port to choose one:\n{listing}")

    print("Multiple badges found:")
    for i, c in enumerate(top, 1):
        print(f"  {i}) {c.device:<20} {c.label}")
    while True:
        try:
            answer = input(f"Select 1-{len(top)} (or q to quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            raise DetectError("No port selected.")
        if answer.lower() in ("q", "quit"):
            raise DetectError("No port selected.")
        if answer.isdigit() and 1 <= int(answer) <= len(top):
            return top[int(answer) - 1].device
