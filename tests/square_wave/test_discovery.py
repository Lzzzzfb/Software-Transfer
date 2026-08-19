from spectrometer.square_wave.discovery import find_square_wave_candidates


PORTS = [
    {
        "port_name": "ttyUSB0",
        "system_location": "/dev/ttyUSB0",
        "serial_number": "MOTOR",
        "vendor_id": 0x1A86,
        "product_id": 0x7523,
    },
    {
        "port_name": "ttyACM1",
        "system_location": "/dev/ttyACM1",
        "serial_number": "WAVE-B",
        "vendor_id": 0x0483,
        "product_id": 0x5740,
    },
    {
        "port_name": "ttyACM0",
        "system_location": "/dev/ttyACM0",
        "serial_number": "WAVE-A",
        "vendor_id": 0x0483,
        "product_id": 0x5740,
    },
    {
        "port_name": "ttyFIQ0",
        "system_location": "/dev/ttyFIQ0",
    },
    {
        "port_name": "ttyS2",
        "system_location": "/dev/ttyS2",
    },
]


def test_candidates_exclude_onboard_and_occupied_ports():
    candidates = find_square_wave_candidates(
        PORTS, excluded_ports={"/dev/ttyACM0", "ttyUSB0"}
    )
    assert [item.port_name for item in candidates] == ["ttyACM1"]


def test_stm32_vid_pid_is_prioritized_but_not_identity_confirmation():
    candidates = find_square_wave_candidates(PORTS)
    assert [item.port_name for item in candidates[:2]] == [
        "ttyACM0",
        "ttyACM1",
    ]
    assert all(item.vendor_id == 0x0483 for item in candidates[:2])


def test_saved_usb_serial_is_prioritized_and_still_returned_as_candidate():
    candidates = find_square_wave_candidates(PORTS, preferred_serial="WAVE-B")
    assert candidates[0].port_name == "ttyACM1"
    assert candidates[0].serial_number == "WAVE-B"


def test_saved_port_is_secondary_preference():
    candidates = find_square_wave_candidates(
        PORTS, preferred_port="/dev/ttyACM1"
    )
    assert candidates[0].port_name == "ttyACM1"
