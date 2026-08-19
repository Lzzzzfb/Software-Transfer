from dataclasses import replace

from spectrometer.qt import QtCore, Signal
from spectrometer.square_wave.controller import SquareWaveController
from spectrometer.square_wave.models import (
    DeviceIdentity,
    DeviceStatus,
    OutputOwner,
    OutputState,
    PortCandidate,
    SquareWaveParameters,
)
from spectrometer.square_wave.settings_store import HostSquareWaveSettings


class FakeTransport(QtCore.QObject):
    connection_changed = Signal(bool, str)
    command_completed = Signal(object, object)
    command_failed = Signal(object, str)
    diagnostic_event = Signal(str)

    def __init__(self):
        super().__init__()
        self.connected = False
        self.port_name = ""
        self.sent = []
        self.shutdown_called = False

    def connect_port(self, port_name, baud_rate=9600):
        self.connected = True
        self.port_name = str(port_name)
        self.connection_changed.emit(True, "")

    def disconnect_port(self):
        was_connected = self.connected
        self.connected = False
        self.port_name = ""
        if was_connected:
            self.connection_changed.emit(False, "disconnected")

    def send_request(self, request, **options):
        if not self.connected:
            self.command_failed.emit(options.get("tag"), "not_connected")
            return False
        self.sent.append((bytes(request), options))
        return True

    def complete_last(self, response):
        _request, options = self.sent[-1]
        self.command_completed.emit(options["tag"], response)

    def fail_last(self, reason):
        _request, options = self.sent[-1]
        self.command_failed.emit(options["tag"], reason)

    def lose_connection(self, reason="device removed"):
        self.connected = False
        self.connection_changed.emit(False, reason)

    def shutdown(self):
        self.shutdown_called = True
        self.connected = False


class FakeSettingsStore:
    def __init__(self, settings=HostSquareWaveSettings()):
        self.settings = settings
        self.saved = []
        self.last_error = ""

    def load(self):
        return self.settings

    def save(self, settings):
        self.settings = settings
        self.saved.append(settings)


PORTS = [
    {
        "port_name": "ttyUSB0",
        "system_location": "/dev/ttyUSB0",
        "serial_number": "MOTOR",
        "vendor_id": 0x1A86,
        "product_id": 0x7523,
    },
    {
        "port_name": "ttyACM0",
        "system_location": "/dev/ttyACM0",
        "serial_number": "WAVE-1",
        "vendor_id": 0x0483,
        "product_id": 0x5740,
    },
]


def make_controller(settings=HostSquareWaveSettings()):
    transport = FakeTransport()
    store = FakeSettingsStore(settings)
    controller = SquareWaveController(
        transport=transport,
        settings_store=store,
        port_provider=lambda: PORTS,
    )
    return controller, transport, store


def connect_successfully(controller, transport, *, running=False):
    assert controller.connect_auto(excluded_ports={"/dev/ttyUSB0"})
    assert transport.sent[-1][0] == b"ID?\r\n"
    transport.complete_last(DeviceIdentity("ZGCAI_SQUARE_WAVE", 1))
    assert transport.sent[-1][0] == b"STATUS?\r\n"
    transport.complete_last(
        DeviceStatus(running, SquareWaveParameters(10, 5))
    )
    if running:
        assert transport.sent[-1][0] == b"STOP\r\n"
        transport.complete_last(object())
        assert transport.sent[-1][0] == b"STATUS?\r\n"
        transport.complete_last(
            DeviceStatus(False, SquareWaveParameters(10, 5))
        )


def test_auto_connect_uses_identity_then_status_and_persists_confirmed_device():
    controller, transport, store = make_controller()
    connections = []
    controller.connection_changed.connect(lambda *args: connections.append(args))

    connect_successfully(controller, transport)

    assert controller.connected
    assert controller.output_state is OutputState.STOPPED
    assert controller.owner is OutputOwner.NONE
    assert controller.identity == DeviceIdentity("ZGCAI_SQUARE_WAVE", 1)
    assert store.saved[-1].usb_serial == "WAVE-1"
    assert store.saved[-1].system_location == "/dev/ttyACM0"
    assert connections[-1] == (True, "ttyACM0")


def test_connect_reconciles_running_device_with_stop_and_status_confirmation():
    controller, transport, _store = make_controller()
    connect_successfully(controller, transport, running=True)

    assert controller.connected
    assert controller.output_state is OutputState.STOPPED
    assert [request for request, _ in transport.sent] == [
        b"ID?\r\n",
        b"STATUS?\r\n",
        b"STOP\r\n",
        b"STATUS?\r\n",
    ]
    assert transport.sent[2][1]["action"] is True


def test_apply_parameters_requires_exact_status_readback_before_persisting():
    controller, transport, store = make_controller()
    connect_successfully(controller, transport)
    saved_before = len(store.saved)
    results = []
    controller.parameters_applied.connect(lambda *args: results.append(args))

    assert controller.apply_parameters(SquareWaveParameters(7, 25))
    assert transport.sent[-1][0] == b"pulse_freq=7\r\n"
    transport.complete_last(object())
    assert transport.sent[-1][0] == b"pulse_width=25\r\n"
    transport.complete_last(object())
    assert transport.sent[-1][0] == b"STATUS?\r\n"
    transport.complete_last(DeviceStatus(False, SquareWaveParameters(7, 25)))

    assert controller.parameters == SquareWaveParameters(7, 25)
    assert len(store.saved) == saved_before + 1
    assert store.saved[-1].parameters == SquareWaveParameters(7, 25)
    assert results[-1][0] is True


def test_manual_start_and_stop_require_status_confirmation_and_manage_owner():
    controller, transport, _store = make_controller()
    connect_successfully(controller, transport)

    assert controller.start_output()
    assert transport.sent[-1][0] == b"START\r\n"
    transport.complete_last(object())
    assert transport.sent[-1][0] == b"STATUS?\r\n"
    transport.complete_last(DeviceStatus(True, SquareWaveParameters(10, 5)))
    assert controller.output_state is OutputState.RUNNING
    assert controller.owner is OutputOwner.MANUAL

    assert controller.stop_output()
    transport.complete_last(object())
    transport.complete_last(DeviceStatus(False, SquareWaveParameters(10, 5)))
    assert controller.output_state is OutputState.STOPPED
    assert controller.owner is OutputOwner.NONE


def test_scan_prepare_always_applies_frozen_parameters_then_starts():
    controller, transport, _store = make_controller()
    connect_successfully(controller, transport)
    prepared = []
    controller.scan_round_prepared.connect(lambda *args: prepared.append(args))

    assert controller.prepare_scan_round(SquareWaveParameters(8, 40))
    transport.complete_last(object())
    transport.complete_last(object())
    transport.complete_last(DeviceStatus(False, SquareWaveParameters(8, 40)))
    transport.complete_last(object())
    transport.complete_last(DeviceStatus(True, SquareWaveParameters(8, 40)))

    assert [request for request, _ in transport.sent[-5:]] == [
        b"pulse_freq=8\r\n",
        b"pulse_width=40\r\n",
        b"STATUS?\r\n",
        b"START\r\n",
        b"STATUS?\r\n",
    ]
    assert controller.owner is OutputOwner.SCAN
    assert prepared[-1][0] is True


def test_scan_cannot_take_over_manual_output_and_finish_releases_scan_owner():
    controller, transport, _store = make_controller()
    connect_successfully(controller, transport)
    failures = []
    finished = []
    controller.operation_failed.connect(failures.append)
    controller.scan_round_finished.connect(lambda *args: finished.append(args))

    controller.start_output()
    transport.complete_last(object())
    transport.complete_last(DeviceStatus(True, SquareWaveParameters(10, 5)))
    assert not controller.prepare_scan_round(SquareWaveParameters())
    assert "手动" in failures[-1]

    controller.stop_output()
    transport.complete_last(object())
    transport.complete_last(DeviceStatus(False, SquareWaveParameters(10, 5)))
    controller.prepare_scan_round(SquareWaveParameters())
    transport.complete_last(object())
    transport.complete_last(object())
    transport.complete_last(DeviceStatus(False, SquareWaveParameters()))
    transport.complete_last(object())
    transport.complete_last(DeviceStatus(True, SquareWaveParameters()))
    assert controller.finish_scan_round()
    transport.complete_last(object())
    transport.complete_last(DeviceStatus(False, SquareWaveParameters()))

    assert controller.owner is OutputOwner.NONE
    assert controller.output_state is OutputState.STOPPED
    assert finished[-1][0] is True


def test_action_timeout_or_disconnect_marks_output_unknown_and_blocks_scan():
    controller, transport, _store = make_controller()
    connect_successfully(controller, transport)
    failures = []
    controller.operation_failed.connect(failures.append)

    controller.start_output()
    transport.fail_last("result_unknown")
    assert controller.output_state is OutputState.UNKNOWN
    assert not controller.prepare_scan_round(SquareWaveParameters())

    transport.lose_connection()
    assert not controller.connected
    assert controller.output_state is OutputState.UNKNOWN
    assert "未知" in failures[-1]


def test_disconnect_while_running_stops_and_confirms_before_closing():
    controller, transport, _store = make_controller()
    connect_successfully(controller, transport)
    controller.start_output()
    transport.complete_last(object())
    transport.complete_last(DeviceStatus(True, SquareWaveParameters()))

    assert controller.disconnect()
    assert transport.connected
    assert transport.sent[-1][0] == b"STOP\r\n"
    transport.complete_last(object())
    transport.complete_last(DeviceStatus(False, SquareWaveParameters()))

    assert not transport.connected
    assert not controller.connected
    assert controller.output_state is OutputState.STOPPED


def test_only_successful_apply_saves_new_parameters():
    controller, transport, store = make_controller()
    connect_successfully(controller, transport)
    baseline = store.saved[-1]
    controller.apply_parameters(SquareWaveParameters(6, 30))
    transport.complete_last(object())
    transport.fail_last("result_unknown")

    assert store.saved[-1] == baseline
    assert controller.output_state is OutputState.UNKNOWN


def test_update_host_settings_does_not_persist_scan_linkage_and_shutdown_reclaims_transport():
    controller, transport, store = make_controller()
    changed = replace(controller.host_settings, automatic_port=False, port_name="COM9")
    assert controller.update_host_settings(changed)
    assert store.saved[-1] == changed

    controller.shutdown()
    assert transport.shutdown_called
