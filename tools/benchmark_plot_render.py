"""Measure software spectrum rendering cost in the active Qt binding."""

import argparse
import json
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spectrometer.qt import QT_API, QtGui, QtWidgets
from spectrometer.ui.plot_widget import SpectrumPlotWidget


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--pixels", type=int, default=3648)
    parser.add_argument("--width", type=int, default=1400)
    parser.add_argument("--height", type=int, default=800)
    parser.add_argument("--iterations", type=int, default=10)
    args = parser.parse_args(argv)
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    plot = SpectrumPlotWidget()
    plot.resize(args.width, args.height)
    x = np.arange(args.pixels, dtype=np.float64)
    y = 32000 + 28000 * np.sin(x / 100)
    plot.update_device_curve(0, x, y, "benchmark")
    image_format = getattr(QtGui.QImage, "Format_ARGB32", None)
    if image_format is None:
        image_format = QtGui.QImage.Format.Format_ARGB32
    image = QtGui.QImage(plot.size(), image_format)
    image.fill(QtGui.QColor("white"))
    times = []
    for _ in range(args.iterations):
        started = time.perf_counter()
        painter = QtGui.QPainter(image)
        plot._paint(painter)
        painter.end()
        times.append((time.perf_counter() - started) * 1000)
    result = {
        "qt_api": QT_API,
        "pixels": args.pixels,
        "size": [args.width, args.height],
        "iterations": args.iterations,
        "minimum_ms": min(times),
        "average_ms": sum(times) / len(times),
        "maximum_ms": max(times),
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
