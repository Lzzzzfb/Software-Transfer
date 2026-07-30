from spectrometer.motor.discovery import (
    MOTOR_USB_PRODUCT_ID,
    MOTOR_USB_VENDOR_ID,
    find_motor_candidates,
)


def test_motor_vid_pid_candidate_is_prioritized_and_spectrometer_is_excluded():
    ports = [
        {
            "port_name": "ttyACM1",
            "vendor_id": 0x9999,
            "product_id": 0x0001,
            "serial_number": "",
            "system_location": "/dev/ttyACM1",
        },
        {
            "port_name": "ttyACM0",
            "vendor_id": MOTOR_USB_VENDOR_ID,
            "product_id": MOTOR_USB_PRODUCT_ID,
            "serial_number": "MOTOR-001",
            "system_location": "/dev/ttyACM0",
        },
        {
            "port_name": "ttyUSB0",
            "vendor_id": 0x1A86,
            "product_id": 0xFE0C,
            "serial_number": "SPECTROMETER",
            "system_location": "/dev/ttyUSB0",
        },
    ]

    candidates = find_motor_candidates(ports, excluded_ports={"ttyUSB0"})

    assert [candidate.port_name for candidate in candidates] == [
        "ttyACM0",
        "ttyACM1",
    ]
    assert candidates[0].preferred is True
    assert candidates[0].stable_id == "MOTOR-001"


def test_only_protocol_handshake_can_confirm_a_candidate():
    candidate = find_motor_candidates(
        [
            {
                "port_name": "COM8",
                "vendor_id": MOTOR_USB_VENDOR_ID,
                "product_id": MOTOR_USB_PRODUCT_ID,
                "serial_number": "",
                "system_location": r"\\.\COM8",
            }
        ]
    )[0]

    assert candidate.preferred is True
    assert candidate.confirmed is False
    assert candidate.stable_id == r"\\.\COM8"

