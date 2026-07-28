import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.communication.protocol import CmdCode, build_packet
from spectrometer.device.device_manager import DeviceManager
from spectrometer.device.spectrometer import SpectrometerDevice
from spectrometer.qt import QtCore, QtWidgets, Slot


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return _APPLICATION


class CaptureWorker(QtCore.QObject):
    def __init__(self):
        super().__init__()
        self.received = []

    @Slot(int, QtCore.QByteArray)
    def do_send_command(self, cmd, params):
        self.received.append((cmd, bytes(params)))


def test_device_manager_queues_command_and_params_for_pyside6_and_pyqt5():
    app = application()
    manager = DeviceManager()
    manager.devices[0] = SpectrometerDevice(0, "COM_TEST")
    worker = CaptureWorker()
    manager._workers[0] = worker

    assert manager.send_to_device(0, CmdCode.GET_VERSION)
    app.processEvents()

    assert worker.received == [(int(CmdCode.GET_VERSION), b"")]
