from cs71_protocol import ProtocolClient as HostProtocolClient
from cs71d.protocol_boundary import ProtocolClient


def test_daemon_reuses_host_protocol_client():
    assert ProtocolClient is HostProtocolClient
