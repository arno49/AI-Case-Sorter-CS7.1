import pytest

from cs71_protocol import CrcError, append_crc, crc16_ccitt_false, format_crc, remove_and_verify_crc


def test_published_ccitt_false_vector_and_protocol_examples():
    assert crc16_ccitt_false(b"123456789") == 0x29B1
    # The enable request is unprotected; its response is the first protected frame.
    assert format_crc(b"@2 crc:off") == "D690"
    assert format_crc(b"@1 done:crc=on") == "CF68"
    assert format_crc(b"@2 done:crc=off") == "48C9"
    assert append_crc("@1 done:crc=on") == "@1 done:crc=on*CF68"


def test_crc_boundaries_and_bad_crc_fail_closed():
    assert remove_and_verify_crc("@1 done", required=False) == "@1 done"
    assert remove_and_verify_crc("@1 done*7EE5", required=True) == "@1 done"
    with pytest.raises(CrcError):
        remove_and_verify_crc("@1 done", required=True)
    with pytest.raises(CrcError):
        remove_and_verify_crc("@1 done*7EE4", required=True)
    with pytest.raises(CrcError):
        remove_and_verify_crc("@1 done*7EE5", required=False)
