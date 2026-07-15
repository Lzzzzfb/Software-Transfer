"""真实光谱仪的低风险协议探测工具。"""

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spectrometer.communication.frame_decoder import FrameDecoder
from spectrometer.communication.protocol import CmdCode, build_packet, parse_packet
from spectrometer.qt import QtCore, QtSerialPort


QSerialPort = QtSerialPort.QSerialPort


def _serial_enum(group_name, value_name):
    if hasattr(QSerialPort, value_name):
        return getattr(QSerialPort, value_name)
    return getattr(getattr(QSerialPort, group_name), value_name)


def _read_write_mode():
    if hasattr(QSerialPort, "ReadWrite"):
        return QSerialPort.ReadWrite
    if hasattr(QtCore.QIODevice, "ReadWrite"):
        return QtCore.QIODevice.ReadWrite
    return QtCore.QIODevice.OpenModeFlag.ReadWrite


def probe(port_name, baud_rate=115200, timeout_seconds=2.0):
    serial = QSerialPort()
    serial.setPortName(port_name)
    serial.setBaudRate(baud_rate)
    serial.setDataBits(_serial_enum("DataBits", "Data8"))
    serial.setParity(_serial_enum("Parity", "NoParity"))
    serial.setStopBits(_serial_enum("StopBits", "OneStop"))
    serial.setFlowControl(_serial_enum("FlowControl", "NoFlowControl"))
    result = {"port": port_name, "baud": baud_rate, "opened": False, "frames": [], "errors": []}
    if not serial.open(_read_write_mode()):
        result["errors"].append(serial.errorString())
        return result
    result["opened"] = True
    try:
        serial.clear()
        request = build_packet(CmdCode.GET_VERSION)
        result["request_hex"] = request.hex(" ")
        if serial.write(request) != len(request):
            result["errors"].append("写入字节数不完整")
        if not serial.waitForBytesWritten(500):
            result["errors"].append(f"发送超时: {serial.errorString()}")

        decoder = FrameDecoder()
        raw = bytearray()
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            remaining_ms = max(1, min(100, int((deadline - time.monotonic()) * 1000)))
            if serial.waitForReadyRead(remaining_ms):
                chunk = bytes(serial.readAll())
                raw.extend(chunk)
                for frame in decoder.feed(chunk):
                    item = {"raw_hex": frame.hex(" "), "length": len(frame)}
                    try:
                        cmd, params = parse_packet(frame)
                        item.update(cmd=f"0x{cmd:02X}", params_hex=params.hex(" "))
                    except ValueError as exc:
                        item["parse_error"] = str(exc)
                    result["frames"].append(item)
                    if frame[3] == CmdCode.GET_VERSION:
                        deadline = time.monotonic()
                        break
        result["raw_hex"] = raw.hex(" ")
        result["decoder_buffer_hex"] = bytes(decoder.buffer).hex(" ")
        result["discarded_bytes"] = decoder.discarded_bytes
        if not raw:
            result["errors"].append("2 秒内未收到任何数据")
    finally:
        serial.close()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ports", nargs="+", help="例如 COM14 COM17 COM18")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--output")
    args = parser.parse_args()
    results = [probe(port, args.baud, args.timeout) for port in args.ports]
    rendered = json.dumps(results, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(rendered, encoding="utf-8")
    return 1 if any(item["errors"] for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
