"""USB serial candidate enumeration for protocol-verified LK-MD2202 discovery."""

from __future__ import annotations

from dataclasses import dataclass

from ..qt import QtSerialPort


@dataclass(frozen=True)
class MotorPortCandidate:
    port_name: str
    system_location: str = ""
    serial_number: str = ""
    vendor_id: int | None = None
    product_id: int | None = None
    description: str = ""
    manufacturer: str = ""
    preferred: bool = False
    confirmed: bool = False

    @property
    def stable_id(self):
        return self.serial_number.strip() or self.system_location.strip() or self.port_name.strip()


def available_port_records():
    records = []
    for info in QtSerialPort.QSerialPortInfo.availablePorts():
        records.append({
            "port_name": info.portName(),
            "system_location": info.systemLocation(),
            "serial_number": info.serialNumber(),
            "vendor_id": info.vendorIdentifier() if info.hasVendorIdentifier() else None,
            "product_id": info.productIdentifier() if info.hasProductIdentifier() else None,
            "description": info.description(),
            "manufacturer": info.manufacturer(),
        })
    return records


def find_motor_candidates(ports=None, *, excluded_ports=frozenset(), preferred_port=""):
    ports = available_port_records() if ports is None else ports
    excluded = {str(item).casefold() for item in excluded_ports}
    result = []
    for record in ports:
        name = str(record.get("port_name") or "")
        location = str(record.get("system_location") or "")
        if name.casefold() in excluded or location.casefold() in excluded:
            continue
        result.append(MotorPortCandidate(
            port_name=name,
            system_location=location,
            serial_number=str(record.get("serial_number") or ""),
            vendor_id=record.get("vendor_id"),
            product_id=record.get("product_id"),
            description=str(record.get("description") or ""),
            manufacturer=str(record.get("manufacturer") or ""),
            preferred=bool(preferred_port and preferred_port in (name, location)),
        ))
    return tuple(sorted(result, key=lambda item: (not item.preferred, item.port_name.casefold())))
