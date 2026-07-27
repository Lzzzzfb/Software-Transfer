from spectrometer.communication.serial_port import (
    CH569W_PRODUCT_ID,
    CH569W_VENDOR_ID,
    DeviceFinder,
)


def port(**overrides):
    value = {
        "port_name": "ttyACM0",
        "system_location": "/dev/ttyACM0",
        "vendor_id": CH569W_VENDOR_ID,
        "product_id": CH569W_PRODUCT_ID,
        "description": "USB serial",
        "manufacturer": "WCH",
        "serial_number": "",
    }
    value.update(overrides)
    return value


def test_only_exact_ch569w_usb_identity_is_a_candidate():
    assert DeviceFinder.is_likely_spectrometer(port())
    assert DeviceFinder.is_likely_spectrometer(port(port_name="ttyACM1"))
    assert not DeviceFinder.is_likely_spectrometer(port(product_id=0x7523))
    assert not DeviceFinder.is_likely_spectrometer(port(vendor_id=0x0483))
    assert not DeviceFinder.is_likely_spectrometer(
        port(vendor_id=0, product_id=0, description="CH340 spectrometer")
    )
    assert not DeviceFinder.is_likely_spectrometer(
        port(vendor_id=None, product_id=None)
    )
