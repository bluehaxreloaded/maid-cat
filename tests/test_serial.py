from serial import check_digit, clean_serial, describe_serial, find_serial, read_serial, serials_match


def test_check_digit():
    assert check_digit("12345678") == 4
    assert check_digit("00000000") == 0


def test_read_serial_with_correct_check_digit():
    info = read_serial("YJM123456784")
    assert info.model == "New 3DS"
    assert info.region == "JPN"
    assert info.known_region_code
    assert info.check_digit_ok
    assert describe_serial(info) == "New 3DS · JPN"


def test_read_serial_with_wrong_check_digit():
    assert not read_serial("YJM123456785").check_digit_ok


def test_read_serial_without_check_digit_is_fine():
    info = read_serial("YJM12345678")
    assert info.check_digit is None
    assert info.check_digit_ok


def test_read_serial_rejects_other_text():
    assert read_serial("hello") is None
    assert read_serial("YJM1234") is None


def test_clean_serial_fixes_typos_and_spaces():
    assert clean_serial("yjm 1234567 8 4") == "YJM123456784"
    assert clean_serial("YJMO234567I4") == "YJM023456714"


def test_unusual_region_codes():
    assert read_serial("YUH12345678").unusual
    assert not read_serial("YJM12345678").unusual


def test_find_serial():
    assert find_serial("my serial is YJM123456784 thanks") == "YJM123456784"
    assert find_serial("no serial here") is None
    assert find_serial(None) is None


def test_serials_match_allows_one_extra_check_digit():
    assert serials_match("YJM123456784", "YJM12345678")
    assert serials_match("YJM12345678", "YJM123456784")
    assert serials_match(" yjm12345678 ", "YJM12345678")
    assert not serials_match("YJM12345679", "YJM12345678")
    assert not serials_match("YJM1234567845", "YJM12345678")
    assert not serials_match("", "YJM12345678")
