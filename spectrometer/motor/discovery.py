"""Motor USB serial candidate discovery.

VID/PID only changes probe order. A port becomes a confirmed motor controller
only after the caller completes the ``ID?`` protocol handshake.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..qt import QtSerialPort


MOTOR_USB_VENDOR_ID = 0x0483
MOTOR_USB_PRODUCT_ID = 0x5740


@dataclass(frozen=True)
class MotorPortCandidate:
    port_name: str
    system_location: str
    serial_number: str
    vendor_id: int | None
    product_id: int | None
    description: str = ""
    manufacturer: str = ""
    preferred: bool = False
    confirmed: bool = False

    @property
    def stable_id(self) -> str:
        return (
            self.serial_number.strip()
            or self.system_location.strip()
            or self.port_name.strip()
        )


def available_port_records() -> list[dict]:
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
    return records


def find_motor_candidates(
    ports: list[dict] | None = None,
    *,
    excluded_ports: set[str] | frozenset[str] = frozenset(),
) -> tuple[MotorPortCandidate, ...]:
    if ports is None:
        ports = available_port_records()
    excluded = {str(port).casefold() for port in excluded_ports}
    candidates = []
    for record in ports:
        port_name = str(record.get("port_name") or "")
        system_location = str(record.get("system_location") or "")
        if (
            port_name.casefold() in excluded
            or system_location.casefold() in excluded
        ):
            continue
        vendor_id = record.get("vendor_id")
        product_id = record.get("product_id")
        preferred = (
            vendor_id == MOTOR_USB_VENDOR_ID
            and product_id == MOTOR_USB_PRODUCT_ID
        )
        candidates.append(
            MotorPortCandidate(
                port_name=port_name,
                system_location=system_location,
                serial_number=str(record.get("serial_number") or ""),
                vendor_id=vendor_id,
                product_id=product_id,
                description=str(record.get("description") or ""),
                manufacturer=str(record.get("manufacturer") or ""),
                preferred=preferred,
            )
        )
    candidates.sort(
        key=lambda candidate: (
            not candidate.preferred,
            candidate.port_name.casefold(),
        )
    )
    return tuple(candidates)


def is_motor_identity(fields: dict[str, str]) -> bool:
    try:
        protocol = int(fields.get("MOTOR_PROTOCOL", ""))
    except ValueError:
        return False
    return fields.get("ID") == "TMC2209" and protocol == 2

