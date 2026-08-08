from pathlib import Path

from cs71_protocol import LineReader, ProtocolClient, ScriptedTransport


def test_existing_v1_golden_preserves_ping_space_and_old_firmware_fallback():
    golden = (Path(__file__).parents[2] / "test" / "fixtures" / "v1-wire-golden.txt").read_text()
    assert "response=\\x20ok\\n" in golden
    assert "request=protocol:2?\\n\nresponse=ok\\n" in golden

    reader = LineReader(ScriptedTransport([b" ok\n"]))
    assert reader.read_line(.02, v2=False) == " ok"
    old = ScriptedTransport([b"ok\r\n"])
    assert ProtocolClient(old, timeout=.02).activate() is False
