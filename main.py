"""ZGCAI 光谱仪采集与分析工作站入口。"""

import argparse
import os
from pathlib import Path
import struct
import sys

from spectrometer.qt import QT_API, QtCore, QtGui, QtWidgets, application_exec


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="ZGCAI 光谱仪采集与分析工作站")
    parser.add_argument("--simulate", action="store_true", help="使用四台模拟光谱仪运行")
    parser.add_argument(
        "--ports",
        nargs="+",
        metavar="COM",
        help="仅自动连接指定串口，例如 --ports COM14 COM17 COM18",
    )
    return parser.parse_args(argv)


def load_stylesheet():
    path = Path(__file__).resolve().parent / "spectrometer" / "ui" / "styles.qss"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def main(argv=None):
    if struct.calcsize("P") * 8 != 64:
        raise RuntimeError("本软件仅支持 64 位 Python / Windows")
    args = parse_args(argv)
    if hasattr(QtCore.Qt, "AA_EnableHighDpiScaling"):
        QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    application = QtWidgets.QApplication(sys.argv[:1])
    application.setApplicationName("ZGCAI 光谱仪工作站")
    application.setOrganizationName("ZGCAI")
    application.setStyle("Fusion")
    application.setStyleSheet(load_stylesheet())
    font = QtGui.QFont("Microsoft YaHei UI", 9)
    application.setFont(font)
    from spectrometer.ui.main_window import MainWindow

    window = MainWindow(simulation=args.simulate, port_allowlist=args.ports)
    window.show()
    return application_exec(application)


if __name__ == "__main__":
    sys.exit(main())
