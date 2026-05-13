#!/usr/bin/env python3
"""Dump every undecoded status surface we have so we can find the
'film loaded' bit by diffing no-film vs film-loaded outputs.

Usage on the Pi:
    sudo /opt/pipalette/.venv/bin/python3 /opt/pipalette/deploy/probe-device.py [label]

Or just run from a fresh checkout — only depends on pp8k.
"""

import datetime
import sys

import pp8k


def hexdump(data, width=16):
    out = []
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        out.append(f"  {i:04x}  {hex_part:<{width*3}}  {ascii_part}")
    return "\n".join(out)


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "probe"
    print(f"=== piPalette device probe — {label} ===")
    print(f"=== {datetime.datetime.now().isoformat(timespec='seconds')} ===")
    print()

    dev = pp8k.open(4)
    try:
        # ---- INQUIRY ----------------------------------------------------
        print("[INQUIRY]")
        info = dev.info
        print(f"  identification: {info.identification}")
        print(f"  product:        {info.product}")
        print(f"  firmware:       {info.firmware}")
        print(f"  revision:       {info.revision!r}")
        print(f"  buffer_kb:      {info.buffer_kb}")
        print(f"  hres_max:       {info.hres_max}  vres_max: {info.vres_max}")
        print()

        # ---- TEST UNIT READY -------------------------------------------
        print("[TEST UNIT READY]")
        try:
            ready = dev.ready
            print(f"  ready: {ready}")
        except Exception as exc:
            print(f"  raised: {type(exc).__name__}: {exc}")
        print()

        # ---- CURRENT_STATUS (DFRCMD sub 6) — raw + decoded -------------
        # _t is the underlying transport. We poke directly to keep the
        # bytes-before-decode for the diff.
        print("[CURRENT_STATUS — raw 7 bytes via DFRCMD sub 6]")
        raw_cs = dev._dev._t.execute(
            bytes([0x0C, 0, 6, 0, 7, 0]), data_in_len=7
        )
        print(hexdump(raw_cs))
        print(f"  bytes 0-1 (buffer_free_kb): {int.from_bytes(raw_cs[0:2], 'big')}")
        print(f"  byte 2 (exposure_state):    {raw_cs[2]:#04x}")
        print(f"  bytes 3-4 (current_line):   {int.from_bytes(raw_cs[3:5], 'big')}")
        print(f"  byte 5 (film_slot):         {raw_cs[5]:#04x}")
        print(f"  byte 6 (status):            {raw_cs[6]:#04x}  ← candidate")
        print()

        # ---- MODE SENSE — raw 61 bytes ---------------------------------
        print("[MODE SENSE — raw 61 bytes via 0x1A]")
        raw_ms = dev._dev._t.execute(
            bytes([0x1A, 0, 0, 0, 61, 0]), data_in_len=61
        )
        print(hexdump(raw_ms))
        print(f"  byte 6 (vendor status):     {raw_ms[6]:#04x}  ← candidate")
        print(f"  byte 8 (film_slot):         {raw_ms[8]:#04x}")
        print()

        # ---- REQUEST SENSE — what does the device want to tell us? -----
        # Issued unconditionally to see whether any pending condition
        # encodes film state. Request 18 bytes so we get ASC/ASCQ too.
        print("[REQUEST SENSE — raw 18 bytes via 0x03]")
        try:
            raw_rs = dev._dev._t.execute(
                bytes([0x03, 0, 0, 0, 18, 0]), data_in_len=18
            )
            print(hexdump(raw_rs))
            if len(raw_rs) >= 13:
                print(f"  byte 2 (sense_key):    {raw_rs[2] & 0x0F:#04x}")
                print(f"  byte 12 (asc):         {raw_rs[12]:#04x}")
                print(f"  byte 13 (ascq):        {raw_rs[13]:#04x}")
        except Exception as exc:
            print(f"  raised: {type(exc).__name__}: {exc}")
        print()

    finally:
        dev.close()


if __name__ == "__main__":
    main()
