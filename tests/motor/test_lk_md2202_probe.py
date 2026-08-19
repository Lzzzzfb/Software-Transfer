from types import SimpleNamespace

from spectrometer.motor.discovery import MotorPortCandidate
from spectrometer.motor.lk_md2202 import AxisConfiguration, LEGACY_IDENTITY_REGISTERS
from spectrometer.motor.probe import ReadOnlyProbe, create_parser
from spectrometer.qt import QtCore, Signal


class FakeTransport(QtCore.QObject):
    connection_changed = Signal(bool, str)
    command_completed = Signal(object, object)
    command_failed = Signal(object, str)

    def __init__(self):
        super().__init__()
        self.connected = False
        self.sent = []

    def connect_port(self, port_name, baud_rate):
        self.connected = True
        self.connection_changed.emit(True, "")

    def disconnect_port(self):
        self.connected = False

    def send_request(self, request, **options):
        self.sent.append((bytes(request), options))
        return True

    def complete_last(self, registers):
        _request, options = self.sent[-1]
        self.command_completed.emit(
            options["tag"], SimpleNamespace(registers=tuple(registers))
        )

    def shutdown(self):
        self.disconnect_port()


def axis_config_registers(config=AxisConfiguration()):
    end_hi, end_lo = divmod(config.end_position_pulses, 0x10000)
    speed_hi, speed_lo = divmod(config.position_speed_pps, 0x10000)
    return [
        int(config.step_angle), int(config.microstep), int(config.run_current),
        int(config.limit_mode), end_hi, end_lo, 0, 0, 0,
        int(config.stop_current), config.acceleration, config.deceleration,
        0, 0, speed_hi, speed_lo,
    ]


def test_probe_help_states_that_it_is_read_only():
    assert "只读" in create_parser().description


def test_probe_cli_defaults_match_driver_defaults():
    args = create_parser().parse_args([])
    assert args.address == 1
    assert args.baud == 9600
    assert args.timeout == 8.0


def test_probe_module_exposes_bounded_abort():
    assert callable(ReadOnlyProbe.abort)


def test_probe_accepts_exact_legacy_identity_and_completes_read_only_chain():
    transport = FakeTransport()
    candidate = MotorPortCandidate("COM8", "COM8", serial_number="RS485-A")
    probe = ReadOnlyProbe((candidate,), transport=transport)
    results = []
    probe.finished.connect(results.append)

    probe.start()
    transport.complete_last(LEGACY_IDENTITY_REGISTERS)
    transport.complete_last(axis_config_registers())
    transport.complete_last(axis_config_registers())
    transport.complete_last((1, 3, 0, 0, 0))
    transport.complete_last((0, 0, 0, 320))
    transport.complete_last((0, 0, 0, 320))

    assert results[0]["ok"] is True
    assert results[0]["device"]["identity"] == {
        "software_version": 0x0064,
        "name": "",
    }
    assert results[0]["device"]["communication"] == {
        "address": 1,
        "baud_rate": 9600,
        "data_bits": 8,
        "stop_bits": 1,
        "parity": 0,
    }
    assert len(transport.sent) == 6
    assert all(request[0][1] == 0x03 for request in transport.sent)
