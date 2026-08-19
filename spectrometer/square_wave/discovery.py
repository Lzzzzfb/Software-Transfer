"""USB serial candidate enumeration for the square-wave generator."""

from __future__ import annotations

from ..qt import QtSerialPort
from .models import PortCandidate


STM32_CDC_VENDOR_ID = 0x0483
STM32_CDC_PRODUCT_ID = 0x5740


def available_port_records() -> tuple[dict, ...]:
    records = []
    for info in QtSerialPort.QSerialPortInfo.availablePorts():
        records.append(
            {
                "port_name": info.portName(),
                "system_location": info.systemLocation(),
                "serial_number": info.serialNumber(),
                "vendor_id": (
                    info.vendorIdentifier()
                    if info.hasVendorIdentifier()
                    else None
                ),
                "product_id": (
                    info.productIdentifier()
                    if info.hasProductIdentifier()
                    else None
                ),
                "description": info.description(),
                "manufacturer": info.manufacturer(),
            }
        )
    return tuple(records)


def _normalized(value) -> str:
    return str(value or "").replace("\\", "/").casefold()


def _is_usb_serial_candidate(record) -> bool:
    name = _normalized(record.get("port_name"))
    location = _normalized(record.get("system_location"))
    leaf = location.rsplit("/", 1)[-1]
    if name.startswith("ttyfiq") or leaf.startswith("ttyfiq"):
        return False
    if name.startswith("ttys") or leaf.startswith("ttys"):
        return False
    if record.get("vendor_id") is not None or record.get("product_id") is not None:
        return True
    return name.startswith(("ttyusb", "ttyacm", "cu.usb", "com")) or (
        leaf.startswith(("ttyusb", "ttyacm", "cu.usb", "com"))
    )


def find_square_wave_candidates(
    ports=None,
    *,
    excluded_ports=frozenset(),
    preferred_serial="",
    preferred_port="",
) -> tuple[PortCandidate, ...]:
    ports = available_port_records() if ports is None else ports
    excluded = {_normalized(item) for item in excluded_ports if str(item)}
    preferred_serial = str(preferred_serial or "").strip().casefold()
    preferred_port = _normalized(preferred_port)
    candidates = []
    for record in ports:
        if not _is_usb_serial_candidate(record):
            continue
        port_name = str(record.get("port_name") or "")
        system_location = str(record.get("system_location") or "")
        if (
            _normalized(port_name) in excluded
            or _normalized(system_location) in excluded
        ):
            continue
        candidates.append(
            PortCandidate(
                port_name=port_name,
                system_location=system_location,
                serial_number=str(record.get("serial_number") or ""),
                description=str(record.get("description") or ""),
                manufacturer=str(record.get("manufacturer") or ""),
                vendor_id=record.get("vendor_id"),
                product_id=record.get("product_id"),
            )
        )

    def sort_key(candidate: PortCandidate):
        serial_preferred = bool(
            preferred_serial
            and candidate.serial_number.strip().casefold() == preferred_serial
        )
        port_preferred = bool(
            preferred_port
            and preferred_port
            in {_normalized(candidate.port_name), _normalized(candidate.system_location)}
        )
        stm32_candidate = (
            candidate.vendor_id == STM32_CDC_VENDOR_ID
            and candidate.product_id == STM32_CDC_PRODUCT_ID
        )
        return (
            not serial_preferred,
            not port_preferred,
            not stm32_candidate,
            candidate.port_name.casefold(),
        )

    return tuple(sorted(candidates, key=sort_key))
