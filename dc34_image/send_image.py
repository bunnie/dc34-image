"""
send_image.py — Send a 128x128 B&W PNG to a device over serial.

Chunk wire format (70 bytes before base64):
  [0:2]   u16  chunk index (big-endian)
  [2:66]  u8*64 pixel data
  [66:70] u32  CRC-32 over bytes [0:66] (big-endian)

Serial protocol:
  Host  -> "image <base64>\n"
  Device -> "OK" | "ERR" | "SUCCESS"
"""

import argparse
import struct
import sys
import time
import zlib
import base64
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is required: pip install Pillow")

try:
    import serial
except ImportError:
    sys.exit("pyserial is required: pip install pyserial")

# -- Image / packing constants ------------------------------------------------

EXPECTED_SIZE   = (128, 128)
TOTAL_PIXELS    = 128 * 128          # 16 384
TOTAL_BYTES     = TOTAL_PIXELS // 8  # 2 048
CHUNK_DATA_SIZE = 64
NUM_CHUNKS      = TOTAL_BYTES // CHUNK_DATA_SIZE   # 32
CHUNK_HDR_SIZE  = 2                  # u16 index
CHUNK_CRC_SIZE  = 4                  # u32 crc32
CHUNK_WIRE_SIZE = CHUNK_HDR_SIZE + CHUNK_DATA_SIZE + CHUNK_CRC_SIZE  # 70

# -- Serial constants ---------------------------------------------------------

BAUD_RATE      = 1_000_000
SERIAL_TIMEOUT = 4.0   # seconds waiting for a response line
RETRY_DELAY    = 0.5   # seconds between retries on ERR
MAX_RETRIES    = 4

DEFAULT_LINE_DELAY = 0.2  # seconds between chunks

# -- Image helpers -------------------------------------------------------------

def validate_image(img: Image.Image) -> tuple[bool, str]:
    """Return (ok, reason).  Accepts mode '1', 'L', or 'RGB' that is B&W."""
    if img.size != EXPECTED_SIZE:
        return False, f"size is {img.size}, expected {EXPECTED_SIZE}"

    # Cheapest check: mode '1' is already 1-bit
    if img.mode == "1":
        return True, "ok"

    # For other modes, verify every pixel is pure black or white
    gray = img.convert("L")
    for px in gray.getdata():
        if px not in (0, 255):
            return False, (
                "image contains non-black-and-white pixels "
                "(use --force to dither-convert)"
            )
    return True, "ok"


def force_convert(image_path: str) -> Image.Image:
    """
    Resize to 128×128 and Floyd-Steinberg dither to 1-bit.
    Saves a *_converted.png next to the original.
    """
    img = Image.open(image_path).convert("L")
    img = img.resize(EXPECTED_SIZE, Image.LANCZOS)
    dither_mode = getattr(Image, "Dither", Image).FLOYDSTEINBERG
    img = img.convert("1", dither=dither_mode)

    p = Path(image_path)
    out_path = p.with_name(p.stem + "_converted.png")
    img.save(out_path)
    print(f"[INFO] Converted image saved -> {out_path}")
    return img


def image_to_bytes(img: Image.Image) -> bytes:
    """
    Produce the 2 048-byte payload that matches the reference .rs output:
      1. Flip horizontally
      2. Convert to 1-bit  (black -> 1, white -> 0)
      3. Pack MSB-first into 512 u32s
      4. Reverse each group-of-4 words  (matches the .rs write order)
      5. Serialize as big-endian u32 bytes
    """
    img = img.transpose(Image.FLIP_LEFT_RIGHT).convert("1")
    pixels = list(img.getdata())

    packed: list[int] = []
    current = 0
    count   = 0
    for p in pixels:
        bit = 0 if p else 1          # white (non-zero in mode "1") -> 0
        current |= bit << (31 - count)
        count += 1
        if count == 32:
            packed.append(current)
            current = 0
            count   = 0
    if count:
        packed.append(current)

    # Mirror the reference script's per-group reversal:
    #   output.write("0x{:08x}, 0x{:08x}, 0x{:08x}, 0x{:08x},\n"
    #                .format(packed[i*4+3], packed[i*4+2], packed[i*4+1], packed[i*4+0]))
    reordered: list[int] = []
    for i in range(512 // 4):
        reordered += [packed[i*4+3], packed[i*4+2], packed[i*4+1], packed[i*4+0]]

    return struct.pack(f">{len(reordered)}I", *reordered)   # big-endian u32s


# -- Chunk helpers -------------------------------------------------------------

def make_chunk(index: int, data: bytes) -> bytes:
    """Build a 70-byte chunk: u16 index || 64-byte data || u32 crc32."""
    assert len(data) == CHUNK_DATA_SIZE
    header  = struct.pack(">H", index)
    payload = header + data
    crc     = zlib.crc32(payload) & 0xFFFF_FFFF
    return payload + struct.pack(">I", crc)


# -- Serial transfer -----------------------------------------------------------

def send_image(port: str, image_path: str, force: bool, line_delay: float) -> None:
    # -- Load / validate ------------------------------------------------------
    try:
        img = Image.open(image_path)
    except Exception as e:
        sys.exit(f"[ERROR] Cannot open image: {e}")

    if force:
        img = force_convert(image_path)
    else:
        ok, reason = validate_image(img)
        if not ok:
            sys.exit(f"[ERROR] Invalid image — {reason}")

    # -- Build byte payload ---------------------------------------------------
    bitmap = image_to_bytes(img)
    assert len(bitmap) == TOTAL_BYTES, f"BUG: expected {TOTAL_BYTES} B, got {len(bitmap)}"

    chunks = [bitmap[i : i + CHUNK_DATA_SIZE] for i in range(0, TOTAL_BYTES, CHUNK_DATA_SIZE)]
    assert len(chunks) == NUM_CHUNKS

    # -- Open serial port -----------------------------------------------------
    try:
        ser = serial.Serial(
            port,
            baudrate  = BAUD_RATE,
            bytesize  = serial.EIGHTBITS,
            parity    = serial.PARITY_NONE,
            stopbits  = serial.STOPBITS_ONE,
            timeout   = SERIAL_TIMEOUT,
        )
    except serial.SerialException as e:
        sys.exit(f"[ERROR] Cannot open serial port {port}: {e}")

    print(f"[INFO] Sending {NUM_CHUNKS} chunks via {port} @ {BAUD_RATE} baud")

    try:
        for idx, chunk_data in enumerate(chunks):
            wire    = make_chunk(idx, chunk_data)
            encoded = base64.b64encode(wire).decode("ascii")
            line    = f"image {encoded}\n"
            line_b  = line.encode("ascii")

            for attempt in range(MAX_RETRIES + 1):
                ser.write(line_b)

                raw = ser.readline()
                response = raw.decode("ascii", errors="replace").strip()

                if response.startswith("[console]"): # local echo
                    print(f"skipping {response}")
                    raw = ser.readline()
                    response = raw.decode("ascii", errors="replace").strip()

                if response == "SUCCESS":
                    print(f"[OK]  Chunk {idx+1:>2}/{NUM_CHUNKS} -> SUCCESS — transfer complete")
                    return

                if response == "OK":
                    print(f"[OK]  Chunk {idx+1:>2}/{NUM_CHUNKS}")
                    break

                if response == "ERR":
                    if attempt < MAX_RETRIES:
                        print(f"[WARN] Chunk {idx+1:>2}/{NUM_CHUNKS} ERR — retry {attempt+1}/{MAX_RETRIES}")
                        time.sleep(RETRY_DELAY)
                    else:
                        sys.exit(f"[ERROR] Chunk {idx+1}/{NUM_CHUNKS} failed after {MAX_RETRIES} retries")
                else:
                    # Unexpected / timeout (readline returns b'' on timeout)
                    if attempt < MAX_RETRIES:
                        print(f"[WARN] Chunk {idx+1:>2}/{NUM_CHUNKS} unexpected response "
                              f"'{response}' — retry {attempt+1}/{MAX_RETRIES}")
                        time.sleep(RETRY_DELAY)
                    else:
                        sys.exit(f"[ERROR] Chunk {idx+1}/{NUM_CHUNKS} — no valid response after retries")

            if line_delay > 0:
                time.sleep(line_delay)

        print("[WARN] All chunks sent but SUCCESS was never received")

    finally:
        ser.close()

# -- Serial transfer -----------------------------------------------------------

def clear(port: str, line_delay: float):
    # -- Open serial port -----------------------------------------------------
    try:
        ser = serial.Serial(
            port,
            baudrate  = BAUD_RATE,
            bytesize  = serial.EIGHTBITS,
            parity    = serial.PARITY_NONE,
            stopbits  = serial.STOPBITS_ONE,
            timeout   = SERIAL_TIMEOUT,
        )
    except serial.SerialException as e:
        sys.exit(f"[ERROR] Cannot open serial port {port}: {e}")

    print(f"[INFO] Sending image clear...")
    line    = "image clear\n"
    line_b  = line.encode("ascii")
    try:
        for attempt in range(MAX_RETRIES + 1):
            ser.write(line_b)

            raw = ser.readline()
            response = raw.decode("ascii", errors="replace").strip()

            if response.startswith("[console]"): # local echo
                print(f"skipping {response}")
                raw = ser.readline()
                response = raw.decode("ascii", errors="replace").strip()

            if response == "CLEAR":
                print(f"[OK]  image cleared")
                return
            else:
                if attempt < MAX_RETRIES:
                    print(f"[WARN] clear not confirmed, retry {attempt+1}/{MAX_RETRIES}")
                    time.sleep(RETRY_DELAY)
                else:
                    sys.exit(f"[ERROR] clear failed after {MAX_RETRIES} retries")
        if line_delay > 0:
            time.sleep(line_delay)

    finally:
        ser.close()


# -- CLI ------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send a 128x128 B&W PNG to a badge via serial"
    )
    parser.add_argument(
        "--port",  required=True,
        help="Serial port, e.g. /dev/ttyUSB0 or COM3"
    )
    parser.add_argument(
        "--image", required=True,
        help="Path to the 128x128 black-and-white PNG"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Force-convert any image to 128x128 B&W (Floyd-Steinberg dither); "
             "saves *_converted.png alongside the original"
    )
    parser.add_argument(
        "--delay", type=float, default=DEFAULT_LINE_DELAY,
        metavar="SECONDS",
        help=f"Delay between chunk lines (default: {DEFAULT_LINE_DELAY}s)"
    )
    parser.add_argument(
        "--clear", action="store_true",
        help="Remove the image. Overrides any other commands when specified"
    )
    args = parser.parse_args()
    if args.clear:
        print("--clear specified, ignoring all other arguments and performing clear")
        clear(args.port, args.delay)
    else:
        send_image(args.port, args.image, args.force, args.delay)


if __name__ == "__main__":
    main()
