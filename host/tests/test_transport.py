import sys
from types import SimpleNamespace

import pytest

from cs71_protocol import DtrSuppressionError, SerialTransport, supports_preopen_dtr_suppression


class PreOpenSerial:
    __module__ = "serial.serialwin32"

    def __init__(self, *, port, **_kwargs):
        assert port is None
        self.events = ["constructed"]
        self._dtr_set = False
        self.is_open = False

    @property
    def dtr(self):
        raise AssertionError("cached raw.dtr must not be read as physical evidence")

    @dtr.setter
    def dtr(self, value):
        assert value is False
        self._dtr_set = True
        self.events.append("dtr-low")

    @property
    def port(self):
        return None

    @port.setter
    def port(self, value):
        assert self._dtr_set
        self.events.append(f"port:{value}")

    def open(self):
        assert self._dtr_set
        self.events.append("open")
        self.is_open = True

    def close(self):
        self.events.append("close")
        self.is_open = False


def install_fake_serial(monkeypatch):
    created = []

    def factory(**kwargs):
        raw = PreOpenSerial(**kwargs)
        created.append(raw)
        return raw

    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=factory))
    return created


def test_preopen_dtr_path_does_not_read_cached_dtr_and_is_injectable(monkeypatch):
    created = install_fake_serial(monkeypatch)

    transport = SerialTransport.open(
        "COM9", dtr_suppression_supported=lambda raw: isinstance(raw, PreOpenSerial)
    )

    assert transport.dtr_suppression_guaranteed is True
    assert created[0].events == ["constructed", "dtr-low", "port:COM9", "open"]
    assert supports_preopen_dtr_suppression(created[0], platform="win32")
    assert not supports_preopen_dtr_suppression(created[0], platform="darwin")


def test_unsupported_dtr_backend_is_rejected_before_port_open(monkeypatch):
    created = install_fake_serial(monkeypatch)

    with pytest.raises(DtrSuppressionError, match="Windows pyserial backend"):
        SerialTransport.open("COM9", dtr_suppression_supported=lambda _raw: False)

    assert created[0].events == ["constructed", "dtr-low", "close"]
