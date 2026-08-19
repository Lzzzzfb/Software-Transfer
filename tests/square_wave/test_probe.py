from spectrometer.qt import QtCore, Signal
from spectrometer.square_wave.models import (
    DeviceIdentity,
    DeviceStatus,
    PortCandidate,
    SquareWaveParameters,
)
from spectrometer.square_wave.probe import ReadOnlySquareWaveProbe, create_parser
from spectrometer.square_wave.protocol import id_command, status_command


class FakeTransport(QtCore.QObject):
    connection_changed = Signal(bool, str)
    command_completed = Signal(object, object)
    command_failed = Signal(object, str)

    def __init__(self):
        super().__init__()
        self.connected = False
        self.sent = []
        self.connected_ports = []

    def connect_port(self, port_name, baud_rate=9600):
        self.connected = True
        self.connected_ports.append((port_name, baud_rate))
        self.connection_changed.emit(True, "")

    def disconnect_port(self):
        self.connected = False

    def send_request(self, request, **options):
        self.sent.append((bytes(request), options))
        return True

    def complete_last(self, response):
        _request, options = self.sent[-1]
        self.command_completed.emit(options["tag"], response)

    def shutdown(self):
        self.disconnect_port()


def test_probe_cli_is_read_only_and_uses_fixed_serial_format():
    parser = create_parser()
    args = parser.parse_args([])

    assert "只读" in parser.description
    assert args.baud == 9600
    assert args.timeout == 8.0


def test_probe_reads_identity_then_status_without_action_commands():
    transport = FakeTransport()
    candidate = PortCandidate("ttyACM0", "/dev/ttyACM0", serial_number="STM32-A")
    probe = ReadOnlySquareWaveProbe((candidate,), transport=transport)
    results = []
    probe.finished.connect(results.append)

    probe.start()
    assert transport.sent[-1][0] == id_command()
    transport.complete_last(DeviceIdentity("ZGCAI_SQUARE_WAVE", 1))
    assert transport.sent[-1][0] == status_command()
    transport.complete_last(DeviceStatus(False, SquareWaveParameters(10, 5)))

    assert results == [{
        "ok": True,
        "device": {
            "port": "ttyACM0",
            "system_location": "/dev/ttyACM0",
            "serial_number": "STM32-A",
            "baud_rate": 9600,
            "identity": {"name": "ZGCAI_SQUARE_WAVE", "protocol_version": 1},
            "status": {"running": False, "frequency_hz": 10, "pulse_width_us": 5},
        },
        "failures": [],
    }]
    assert [request for request, _options in transport.sent] == [
        b"ID?\r\n",
        b"STATUS?\r\n",
    ]
    assert all(not options["action"] for _request, options in transport.sent)


def test_probe_rejects_wrong_identity_and_tries_next_candidate():
    transport = FakeTransport()
    first = PortCandidate("COM7", "COM7")
    second = PortCandidate("COM8", "COM8")
    probe = ReadOnlySquareWaveProbe((first, second), transport=transport)
    results = []
    probe.finished.connect(results.append)

    probe.start()
    transport.complete_last(DeviceIdentity("OTHER_DEVICE", 1))
    assert transport.connected_ports[-1] == ("COM8", 9600)
    transport.complete_last(DeviceIdentity("ZGCAI_SQUARE_WAVE", 1))
    transport.complete_last(DeviceStatus(True, SquareWaveParameters(7, 25)))

    assert results[0]["ok"] is True
    assert results[0]["device"]["port"] == "COM8"
    assert results[0]["failures"] == [{
        "port": "COM7",
        "reason": "unsupported identity: OTHER_DEVICE protocol=1",
    }]
