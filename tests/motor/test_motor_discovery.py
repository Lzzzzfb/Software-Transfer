from spectrometer.motor.discovery import find_motor_candidates


PORTS = [
    {
        "port_name": "ttyUSB1",
        "system_location": "/dev/ttyUSB1",
        "serial_number": "RS485-B",
        "vendor_id": 0x0403,
        "product_id": 0x6001,
    },
    {
        "port_name": "ttyUSB0",
        "system_location": "/dev/ttyUSB0",
        "serial_number": "RS485-A",
        "vendor_id": 0x1A86,
        "product_id": 0x7523,
    },
]


ONBOARD_AND_USB_PORTS = [
    {
        "port_name": "ttyFIQ0",
        "system_location": "/dev/ttyFIQ0",
        "description": "FIQ Debugger",
    },
    {
        "port_name": "ttyFIQ1",
        "system_location": "/dev/ttyFIQ1",
    },
    {
        "port_name": "ttyS2",
        "system_location": "/dev/ttyS2",
    },
    {
        "port_name": "ttyUSB0",
        "system_location": "/dev/ttyUSB0",
        "vendor_id": 0x1A86,
        "product_id": 0x7523,
    },
    {
        "port_name": "ttyACM0",
        "system_location": "/dev/ttyACM0",
    },
]


def test_saved_port_is_prioritized_and_spectrometer_port_is_excluded():
    candidates = find_motor_candidates(
        PORTS,
        excluded_ports={"/dev/ttyUSB0"},
        preferred_port="/dev/ttyUSB1",
    )
    assert [candidate.port_name for candidate in candidates] == ["ttyUSB1"]
    assert candidates[0].preferred is True
    assert candidates[0].stable_id == "RS485-B"


def test_usb_metadata_never_confirms_lk_md2202_identity():
    candidate = find_motor_candidates(PORTS)[0]
    assert candidate.confirmed is False


def test_candidates_are_stable_and_preferred_port_sorts_first():
    candidates = find_motor_candidates(PORTS, preferred_port="ttyUSB1")
    assert [candidate.port_name for candidate in candidates] == [
        "ttyUSB1",
        "ttyUSB0",
    ]


def test_auto_discovery_excludes_ttyfiq_and_non_usb_onboard_uarts():
    candidates = find_motor_candidates(ONBOARD_AND_USB_PORTS)
    assert [candidate.port_name for candidate in candidates] == [
        "ttyACM0",
        "ttyUSB0",
    ]
