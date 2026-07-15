"""真实设备单帧采集验收，不修改持久参数。"""

import argparse
import json
from pathlib import Path
import statistics
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spectrometer.communication.frame_decoder import FrameDecoder
from spectrometer.communication.protocol import CmdCode, build_packet, parse_data_packet, parse_packet
from tools.hardware_profile import open_port


class CollectTimeout(TimeoutError):
    def __init__(self, message, raw, frames):
        super().__init__(message); self.raw = bytes(raw); self.frames = list(frames)


def send(serial, command):
    packet = build_packet(command)
    if serial.write(packet) != len(packet): raise OSError("写入字节数不完整")
    if not serial.waitForBytesWritten(500): raise TimeoutError(f"0x{command:02X} 发送超时")


def collect_until(serial, decoder, wanted, timeout):
    deadline = time.monotonic() + timeout; frames = []; raw = bytearray()
    while time.monotonic() < deadline:
        wait_ms = max(1, min(100, int((deadline - time.monotonic()) * 1000)))
        if not serial.waitForReadyRead(wait_ms): continue
        chunk = bytes(serial.readAll()); raw.extend(chunk)
        for frame in decoder.feed(chunk):
            frames.append(frame)
            if frame[3] == wanted: return frame, frames
    raise CollectTimeout(f"等待 0x{wanted:02X} 超时", raw, frames)


def ordinary_exchange(serial, decoder, command, timeout=2.0):
    send(serial, command); frame, seen = collect_until(serial, decoder, command, timeout)
    cmd, params = parse_packet(frame); return params, seen


def acquire_one(port, output_directory, baud=115200, initialize=True):
    result = {"port": port, "baud": baud, "errors": [], "events": []}
    try: serial = open_port(port, baud)
    except OSError as exc: result["errors"].append(str(exc)); return result
    decoder = FrameDecoder(); started = time.monotonic()
    try:
        version_params, _ = ordinary_exchange(serial, decoder, CmdCode.GET_VERSION)
        if len(version_params) < 40: raise ValueError("版本信息不足 40 字节")
        values = struct.unpack("<IIffIIIIII", version_params[:40])
        pixel_count, start_pixel, valid_pixel = values[5], values[6], values[7]
        result["device"] = {"device_type": values[1], "firmware": values[3], "serial": values[4],
                            "pixel_count": pixel_count, "start_pixel": start_pixel, "valid_pixel": valid_pixel}

        if initialize:
            send(serial, CmdCode.DEVICE_INIT)
            try:
                init_frame, _ = collect_until(serial, decoder, CmdCode.DEVICE_INIT, timeout=0.8)
                _, init_params = parse_packet(init_frame)
                result["events"].append({"command": "0x10", "params_hex": init_params.hex(" ")})
            except TimeoutError:
                # 三台现有固件均执行初始化但不返回函数表声明的 0x61 ACK。
                result["events"].append({"command": "0x10", "warning": "未返回 ACK，继续采集"})

        send(serial, CmdCode.START_SINGLE)
        data_frame, seen = collect_until(serial, decoder, CmdCode.DATA_TRANSMIT, timeout=6.0)
        for frame in seen:
            if frame[3] != CmdCode.DATA_TRANSMIT:
                try:
                    cmd, params = parse_packet(frame)
                    result["events"].append({"command": f"0x{cmd:02X}", "params_hex": params.hex(" ")})
                except ValueError as exc:
                    result["events"].append({"parse_error": str(exc), "raw_hex": frame.hex(" ")})
        parsed = parse_data_packet(data_frame, n_pixel=pixel_count,
                                   n_start_pixel=start_pixel, n_valid_pixel=valid_pixel)
        pixels = parsed["pixels"]
        output_directory.mkdir(parents=True, exist_ok=True)
        raw_path = output_directory / f"{port}_single_frame.bin"
        raw_path.write_bytes(data_frame)
        result["frame"] = {
            "raw_path": str(raw_path), "raw_length": len(data_frame),
            "n_length": int.from_bytes(data_frame[1:3], "little"),
            "packet_number": parsed["packet_number"], "frame_sequence": parsed["frame_sequence"],
            "reserved": parsed["reserved"], "source_pixel_count": parsed["source_pixel_count"],
            "valid_pixel_count": parsed["pixel_count"], "checksum": parsed["checksum"],
            "minimum": min(pixels), "maximum": max(pixels), "mean": statistics.fmean(pixels),
            "first_16": pixels[:16], "last_16": pixels[-16:],
        }
        try:
            stop_params, _ = ordinary_exchange(serial, decoder, CmdCode.STOP_ACQUISITION, timeout=2.0)
            result["events"].append({"command": "0x52", "params_hex": stop_params.hex(" ")})
        except TimeoutError as exc:
            result["events"].append({"command": "0x52", "warning": str(exc)})
    except (OSError, TimeoutError, ValueError) as exc:
        result["errors"].append(str(exc))
        captured = getattr(exc, "raw", b"")
        buffered = bytes(decoder.buffer)
        result["decoder_debug"] = {
            "captured_length": len(captured),
            "captured_head_hex": captured[:96].hex(" "),
            "captured_tail_hex": captured[-96:].hex(" ") if captured else "",
            "buffer_length": len(buffered),
            "buffer_head_hex": buffered[:64].hex(" "),
            "buffer_tail_hex": buffered[-64:].hex(" ") if buffered else "",
            "discarded_bytes": decoder.discarded_bytes,
            "invalid_headers": decoder.invalid_headers,
            "serial_bytes_available": int(serial.bytesAvailable()),
            "serial_error": serial.errorString(),
            "serial_error_code": int(serial.error()),
        }
    finally:
        result["elapsed_seconds"] = time.monotonic() - started; serial.close()
    return result


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("ports", nargs="+")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--output-directory", default="data/validation")
    parser.add_argument("--skip-init", action="store_true")
    parser.add_argument("--output"); args = parser.parse_args()
    directory = Path(args.output_directory)
    results = [acquire_one(port, directory, args.baud, not args.skip_init) for port in args.ports]
    rendered = json.dumps(results, ensure_ascii=False, indent=2); print(rendered)
    output = Path(args.output) if args.output else directory / "single_acquisition.json"
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text(rendered, encoding="utf-8")
    return 1 if any(item["errors"] for item in results) else 0


if __name__ == "__main__": raise SystemExit(main())
