import hashlib
import struct

# essential.exefs is an ExeFS container built by GodMode9.
# Header (0x200 bytes): 10 file entries (8-byte name, u32 offset, u32 size), then at 0xC0
# one SHA-256 hash per entry, stored in reverse order. File data starts right after the header.
HEADER_SIZE = 0x200
MAX_FILES = 10
HASHES_OFFSET = 0xC0
REQUIRED_FILES = ("secinfo", "otp")


class InvalidEssential(Exception):
    """The file is not a valid essential.exefs."""


def read_essential(data: bytes) -> dict[str, bytes]:
    """Check an essential.exefs and return its files by name. Raises InvalidEssential if it isn't valid."""
    if len(data) < HEADER_SIZE:
        raise InvalidEssential("file is too small")

    files = {}
    for i in range(MAX_FILES):
        entry = data[i * 0x10 : i * 0x10 + 0x10]
        raw_name = entry[:8].rstrip(b"\x00")
        if not raw_name:
            continue
        try:
            name = raw_name.decode("ascii")
        except UnicodeDecodeError:
            raise InvalidEssential("header is not an ExeFS header")
        offset, size = struct.unpack("<II", entry[8:])
        start = HEADER_SIZE + offset
        if start + size > len(data):
            raise InvalidEssential(f"{name} is cut off")
        content = data[start : start + size]

        hash_start = HASHES_OFFSET + (MAX_FILES - 1 - i) * 0x20
        if hashlib.sha256(content).digest() != data[hash_start : hash_start + 0x20]:
            raise InvalidEssential(f"{name} does not match its hash")
        files[name] = content

    for name in REQUIRED_FILES:
        if name not in files:
            raise InvalidEssential(f"{name} is missing")
    # secinfo is 0x111 bytes: signature, then region at 0x100 and serial from 0x102
    if len(files["secinfo"]) < 0x111:
        raise InvalidEssential("secinfo is too small")
    return files


def serial_from_secinfo(secinfo: bytes) -> str:
    """Serial number stored in secinfo (same place soap-cat reads it from)."""
    return secinfo[0x102:0x112].replace(b"\x00", b"").decode("ascii", errors="ignore").strip().upper()


def serials_match(entered: str, from_file: str) -> bool:
    """Whether the serial the helpee entered matches the one in their file.
    The file has 8 digits, while the sticker adds a 9th check digit, so one extra digit on either side still matches."""
    entered = entered.strip().upper()
    if not entered or not from_file:
        return False
    if entered == from_file:
        return True
    longer, shorter = (entered, from_file) if len(entered) > len(from_file) else (from_file, entered)
    return len(longer) == len(shorter) + 1 and longer.startswith(shorter)
