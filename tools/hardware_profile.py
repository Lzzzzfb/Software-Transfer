"""读取真实设备当前参数，不修改设备配置。"""

import argparse
import json
from pathlib import Path
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spectrometer.communication.frame_decoder import FrameDecoder
from spectrometer.communication.protocol import CmdCode, build_packet, parse_packet
from spectrometer.qt import QtCore, QtSerialPort


QSerialPort = QtSerialPort.QSerialPort
QUERY_COMMANDS = [0x01, 0x02, 0x03, 0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x3B, 0x3C]


def _enum(group, name):
    return getattr(QSerialPort, name) if hasattr(QSerialPort, name) else getattr(getattr(QSerialPort, group), name)


def _read_write():
    if hasattr(QSerialPort, "ReadWrite"): return QSerialPort.ReadWrite
    if hasattr(QtCore.QIODevice, "ReadWrite"): return QtCore.QIODevice.ReadWrite
    return QtCore.QIODevice.OpenModeFlag.ReadWrite


def open_port(name, baud):
    serial = QSerialPort(); serial.setPortName(name); serial.setBaudRate(baud)
    serial.setDataBits(_enum("DataBits", "Data8")); serial.setParity(_enum("Parity", "NoParity"))
    serial.setStopBits(_enum("StopBits", "OneStop")); serial.setFlowControl(_enum("FlowControl", "NoFlowControl"))
    if not serial.open(_read_write()): raise OSError(serial.errorString())
    serial.clear(); return serial


def exchange(serial, decoder, command, timeout=2.0):
    request = build_packet(command)
    serial.write(request)
    if not serial.waitForBytesWritten(500):
        raise TimeoutError(f"0x{command:02X} 发送超时: {serial.errorString()}")
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        wait_ms = max(1, min(100, int((deadline - time.monotonic()) * 1000)))
        if not serial.waitForReadyRead(wait_ms): continue
        chunk = bytes(serial.readAll())
        for frame in decoder.feed(chunk):
            item = {"raw_hex": frame.hex(" "), "length": len(frame)}
            try:
                cmd, params = parse_packet(frame)
                item.update(cmd=cmd, params=params)
            except ValueError as exc:
                item["parse_error"] = str(exc); seen.append(item); continue
            seen.append(item)
            if cmd == command:
                return item, seen
    raise TimeoutError(f"0x{command:02X} 在 {timeout:.1f}s 内无响应")


def decode_value(command, params):
    if command == 0x01 and len(params) >= 40:
        name, dev_type, hw, fw, serial, pixels, start, valid, minimum, maximum = struct.unpack("<IIffIIIIII", params[:40])
        return {"name": name, "device_type": dev_type, "hardware": hw, "firmware": fw,
                "serial": serial, "pixel_count": pixels, "start_pixel": start,
                "valid_pixel": valid, "exposure_min_us": minimum, "exposure_max_us": maximum}
    if command in (0x30, 0x32, 0x33, 0x36, 0x3B) and len(params) >= 4:
        return {"u32": struct.unpack("<I", params[:4])[0]}
    if command in (0x03, 0x31, 0x34, 0x35, 0x38) and params:
        return {"u8": params[0]}
    if command == 0x37 and len(params) >= 16:
        c4, c3, c2, c1 = struct.unpack("<4f", params[:16])
        return {"wire_order": [c4, c3, c2, c1], "model_order": [c1, c2, c3, c4]}
    if command == 0x3C:
        serial_text = params[:32].rstrip(b"\x00").decode("ascii", errors="replace").strip()
        return {"production_serial": serial_text, "extra_ascii": params[32:].decode("ascii", errors="replace").rstrip("\x00")}
    return {"params_hex": params.hex(" ")}


def profile(port, baud, timeout):
    result = {"port": port, "baud": baud, "queries": {}, "errors": []}
    try:
        serial = open_port(port, baud)
    except OSError as exc:
        result["errors"].append(str(exc)); return result
    decoder = FrameDecoder()
    try:
        for command in QUERY_COMMANDS:
            key = f"0x{command:02X}"
            try:
                response, seen = exchange(serial, decoder, command, timeout)
                params = response.pop("params")
                response["cmd"] = f"0x{response['cmd']:02X}"
                response["value"] = decode_value(command, params)
                result["queries"][key] = response
            except (OSError, TimeoutError) as exc:
                result["queries"][key] = {"error": str(exc)}
                result["errors"].append(str(exc))
    finally:
        serial.close()
    return result


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("ports", nargs="+")
    parser.add_argument("--baud", type=int, default=115200); parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--output"); args = parser.parse_args()
    results = [profile(port, args.baud, args.timeout) for port in args.ports]
    rendered = json.dumps(results, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True); Path(args.output).write_text(rendered, encoding="utf-8")
    return 1 if any(item["errors"] for item in results) else 0


if __name__ == "__main__": raise SystemExit(main())
