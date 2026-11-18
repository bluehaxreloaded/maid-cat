import pytest

from exefs import HEADER_SIZE, InvalidEssential, read_essential, rebuild_essential, serial_from_secinfo


def _secinfo(serial: bytes = b"YJM12345678") -> bytes:
    secinfo = bytearray(0x111)
    secinfo[0x102 : 0x102 + len(serial)] = serial
    return bytes(secinfo)


def _files() -> dict[str, bytes]:
    return {"secinfo": _secinfo(), "otp": bytes(range(256)), "movable": b"\x01" * 0x140}


def test_rebuilt_file_reads_back_the_same():
    files = _files()
    assert read_essential(rebuild_essential(files)) == files


def test_rebuild_leaves_out_unknown_files():
    files = _files() | {"evil": b"x" * 16}
    assert "evil" not in read_essential(rebuild_essential(files))


def test_serial_from_secinfo():
    assert serial_from_secinfo(_secinfo(b"yjm12345678")) == "YJM12345678"


def test_tampered_file_fails_its_hash():
    data = bytearray(rebuild_essential(_files()))
    data[HEADER_SIZE] ^= 0xFF
    with pytest.raises(InvalidEssential, match="hash"):
        read_essential(bytes(data))


def test_missing_otp():
    files = _files()
    del files["otp"]
    with pytest.raises(InvalidEssential, match="otp is missing"):
        read_essential(rebuild_essential(files))


def test_wrong_size_secinfo():
    files = _files() | {"secinfo": b"\x00" * 0x100}
    with pytest.raises(InvalidEssential, match="secinfo is the wrong size"):
        read_essential(rebuild_essential(files))


@pytest.mark.parametrize("size", [0, HEADER_SIZE - 1, 0x4001])
def test_wrong_file_size(size):
    with pytest.raises(InvalidEssential):
        read_essential(b"\x00" * size)
