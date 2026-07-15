"""用写回相同值的方式验证真实设备设置/控制通道。"""

import argparse
import json
from pathlib import Path
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from spectrometer.communication.frame_decoder import FrameDecoder
from spectrometer.communication.protocol import build_packet, parse_packet
from tools.hardware_profile import open_port


SETTINGS = (
    ("integration_time", 0x30, 0x20, "<I"),
    ("trigger_mode", 0x31, 0x21, "<B"),
    ("interval", 0x32, 0x22, "<I"),
    ("average_count", 0x33, 0x23, "<I"),
    ("dark_current", 0x34, 0x24, "<B"),
    ("gain", 0x35, 0x25, "<B"),
    ("baud_rate", 0x36, 0x26, "<I"),
    ("smooth_count", 0x38, 0x28, "<I"),
    ("trigger_delay", 0x3B, 0x2B, "<I"),
)


def send_and_wait(serial, decoder, command, params=b"", timeout=1.5):
    packet = build_packet(command, params); written = serial.write(packet)
    bytes_written = serial.waitForBytesWritten(500)
    deadline = time.monotonic() + timeout; raw = bytearray(); frames = []
    while time.monotonic() < deadline:
        if not serial.waitForReadyRead(100): continue
        chunk = bytes(serial.readAll()); raw.extend(chunk)
        for frame in decoder.feed(chunk):
            cmd, response = parse_packet(frame); frames.append({"cmd": f"0x{cmd:02X}", "params_hex": response.hex(" "), "raw_hex": frame.hex(" ")})
            if cmd == command:
                return {"request_hex": packet.hex(" "), "written": written, "bytes_written": bytes_written,
                        "response": frames[-1], "serial_error": serial.errorString()}
    return {"request_hex": packet.hex(" "), "written": written, "bytes_written": bytes_written,
            "timeout": True, "raw_hex": raw.hex(" "), "frames": frames,
            "serial_error": serial.errorString(), "serial_error_code": int(serial.error())}


def probe(port, baud=115200):
    result = {"port": port, "baud": baud, "settings": {}, "errors": []}
    serial = open_port(port, baud); decoder = FrameDecoder()
    try:
        for name, query_cmd, set_cmd, fmt in SETTINGS:
            before = send_and_wait(serial, decoder, query_cmd, timeout=2.5)
            if "response" not in before:
                result["settings"][name] = {"before": before, "passed": False}
                result["errors"].append(f"{name}: 读取当前值失败")
                continue
            before_params = bytes.fromhex(before["response"]["params_hex"])
            value_size = struct.calcsize(fmt)
            if len(before_params) < value_size:
                result["settings"][name] = {"before": before, "passed": False}
                result["errors"].append(f"{name}: 响应长度不足")
                continue
            current_value = struct.unpack(fmt, before_params[:value_size])[0]
            set_result = send_and_wait(serial, decoder, set_cmd, struct.pack(fmt, current_value), timeout=2.5)
            after = send_and_wait(serial, decoder, query_cmd, timeout=2.5)
            ack = bytes.fromhex(set_result.get("response", {}).get("params_hex", ""))
            after_params = bytes.fromhex(after.get("response", {}).get("params_hex", ""))
            after_value = struct.unpack(fmt, after_params[:value_size])[0] if len(after_params) >= value_size else None
            passed = bool(ack and 0x60 <= ack[0] <= 0x6F and after_value == current_value)
            result["settings"][name] = {
                "value": current_value,
                "ack": f"0x{ack[0]:02X}" if ack else None,
                "readback": after_value,
                "passed": passed,
            }
            if not passed:
                result["errors"].append(f"{name}: ACK 或回读不一致")
    finally:
        serial.close()
    return result


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("ports", nargs="+"); parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--output", default="data/validation/control_probe.json"); args = parser.parse_args()
    results = [probe(port, args.baud) for port in args.ports]
    rendered = json.dumps(results, ensure_ascii=False, indent=2); print(rendered)
    path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(rendered, encoding="utf-8")
    return 1 if any(item["errors"] for item in results) else 0


if __name__ == "__main__": raise SystemExit(main())
