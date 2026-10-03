import hashlib
import struct
import constants

# essential.exefs is an ExeFS container built by GodMode9.
# Header (0x200 bytes): 10 file entries (8-byte name, u32 offset, u32 size), then at 0xC0
# one SHA-256 hash per entry, stored in reverse order. File data starts right after the header.
HEADER_SIZE = 0x200
MAX_FILES = 10
HASHES_OFFSET = 0xC0
REQUIRED_FILES = ("secinfo", "otp")
# Largest size each file GodMode9 puts in essential.exefs can be. A real one is 0x2200 bytes in total.
KNOWN_FILES = {
    "nand_hdr": 0x200,
    "secinfo": 0x111,
    "movable": 0x140,
    "frndseed": 0x110,
    "nand_cid": 0x10,
    "otp": 0x100,
    "hwcal0": 0x9D0,
    "hwcal1": 0x9D0,
}
MAX_ESSENTIAL_SIZE = 0x4000  # anything bigger isn't an essential.exefs, so don't even read it


class InvalidEssential(Exception):
    """The file is not a valid essential.exefs."""


def read_essential(data: bytes) -> dict[str, bytes]:
    """Check an essential.exefs and return its files by name. Raises InvalidEssential if it isn't valid."""
    if len(data) < HEADER_SIZE:
        raise InvalidEssential("file is too small")
    if len(data) > MAX_ESSENTIAL_SIZE:
        raise InvalidEssential("file is too big")

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
        if name in files:
            raise InvalidEssential(f"{name} is in the file twice")
        offset, size = struct.unpack("<II", entry[8:])
        if name in KNOWN_FILES and size > KNOWN_FILES[name]:
            raise InvalidEssential(f"{name} is too big")
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
    if len(files["secinfo"]) != KNOWN_FILES["secinfo"]:
        raise InvalidEssential("secinfo is the wrong size")
    if len(files["otp"]) != KNOWN_FILES["otp"]:
        raise InvalidEssential("otp is the wrong size")
    return files


def rebuild_essential(files: dict[str, bytes]) -> bytes:
    """A fresh essential.exefs made from only the known files, laid out the way GodMode9 does it.
    Anything else in the uploaded file (unknown files, padding, trailing data) is left out,
    so this is what gets stored instead of the upload itself."""
    header = bytearray(HEADER_SIZE)
    body = bytearray()
    names = [name for name in KNOWN_FILES if name in files]
    for i, name in enumerate(names):
        content = files[name]
        header[i * 0x10 : i * 0x10 + 0x10] = name.encode("ascii").ljust(8, b"\x00") + struct.pack(
            "<II", len(body), len(content)
        )
        hash_start = HASHES_OFFSET + (MAX_FILES - 1 - i) * 0x20
        header[hash_start : hash_start + 0x20] = hashlib.sha256(content).digest()
        body += content
        body += bytes(-len(body) % 0x200)  # each file starts on a 0x200 boundary
    return bytes(header + body)


# secinfo is signed by Nintendo (RSA-2048, PKCS#1 v1.5, SHA-256) over its region and serial.
# The public key is read from constants.py (SECINFO_RETAIL_N, same as soap-cat's cleaninty config)
# rather than kept in this repo, and is only used if it's the genuine retail key.
SECINFO_RETAIL_FINGERPRINT = "e109d0cce7298a46255bea27240f1630ac6cdaccb1b47a90d613171ac2ddfdda"  # cleaninty's hash of it
SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _secinfo_key() -> int | None:
    n = getattr(constants, "SECINFO_RETAIL_N", None)
    if not n:
        return None
    n = int(n)
    if hashlib.sha256(hex(n).encode("ascii")).hexdigest() != SECINFO_RETAIL_FINGERPRINT:
        return None
    return n


def secinfo_signed(secinfo: bytes) -> bool | None:
    """Whether secinfo is signed by Nintendo, or None if the key isn't set up."""
    n = _secinfo_key()
    if n is None:
        return None
    signature, data = secinfo[:0x100], secinfo[0x100:0x111]
    decoded = pow(int.from_bytes(signature, "big"), 0x10001, n).to_bytes(256, "big")
    expected = SHA256_DIGEST_INFO + hashlib.sha256(data).digest()
    return decoded == b"\x00\x01" + b"\xff" * (256 - 3 - len(expected)) + b"\x00" + expected


def serial_from_secinfo(secinfo: bytes) -> str:
    """Serial number stored in secinfo (same place soap-cat reads it from)."""
    return secinfo[0x102:0x112].replace(b"\x00", b"").decode("ascii", errors="ignore").strip().upper()
