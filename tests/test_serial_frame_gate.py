from spectrometer.communication.protocol import CmdCode, build_packet
from spectrometer.communication.serial_port import SerialWorker
import spectrometer.communication.serial_port as serial_module


def data_frame(sequence, pixels=(100, 200)):
    pixel_bytes = b"".join(value.to_bytes(2, "big") for value in pixels)
    length = 6 + len(pixel_bytes)
    packet_number = sequence << 8
    return (
        b"\x24"
        + length.to_bytes(2, "little")
        + bytes([CmdCode.DATA_TRANSMIT])
        + packet_number.to_bytes(4, "little")
        + pixel_bytes
        + b"\x00"
    )


def test_serial_worker_emits_at_most_one_frame_per_gate_interval(monkeypatch):
    worker = SerialWorker(0, "TEST", 115200)
    worker._n_pixel = 2
    emitted = []
    worker.frame_received.connect(emitted.append)
    times = iter(
        (
            1_000_000_000,
            1_000_000_000,
            1_010_000_000,
            1_060_000_000,
            1_060_000_000,
        )
    )
    monkeypatch.setattr(serial_module.time, "monotonic_ns", lambda: next(times))

    worker._handle_packet(data_frame(10))
    worker._handle_packet(data_frame(11))
    worker._handle_packet(data_frame(12))

    assert [frame.sequence for frame in emitted] == [10, 12]
    assert emitted[1].intentionally_skipped == 1


def test_control_response_is_not_subject_to_data_frame_gate():
    worker = SerialWorker(0, "TEST", 115200)
    responses = []
    worker.response_ready.connect(
        lambda device_id, cmd, params: responses.append(
            (device_id, cmd, bytes(params))
        )
    )

    worker._handle_packet(build_packet(CmdCode.GET_VERSION, b"\x01\x02"))

    assert responses == [(0, int(CmdCode.GET_VERSION), b"\x01\x02")]
