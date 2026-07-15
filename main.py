#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ZGCAI 光谱仪上位机控制软件 — 主入口

基于 Python + PyQt5 开发的光谱仪上位机控制软件。
支持多通道光谱仪控制、实时数据采集与可视化分析。

使用方法:
    python main.py

依赖安装:
    pip install -r requirements.txt
"""

import sys
import os

# ---- 修复 Qt 平台插件找不到的问题 ----
# 显式设置 Qt 插件路径，避免非ASCII路径导致 DLL 加载失败
import PyQt5
_qt_pkg_dir = os.path.dirname(PyQt5.__file__)
_qt_plugins_dir = os.path.join(_qt_pkg_dir, 'Qt5', 'plugins')
_qt_bin_dir = os.path.join(_qt_pkg_dir, 'Qt5', 'bin')
os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = os.path.join(_qt_plugins_dir, 'platforms')
os.environ['QT_PLUGIN_PATH'] = _qt_plugins_dir

# Python 3.8+ 使用 os.add_dll_directory 添加 DLL 搜索路径
if hasattr(os, 'add_dll_directory'):
    for _d in [_qt_bin_dir, os.path.join(_qt_pkg_dir, 'Qt5')]:
        try:
            os.add_dll_directory(_d)
        except (OSError, FileNotFoundError):
            pass

# 确保项目路径在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt


def main():
    """应用程序主入口"""
    # 高DPI适配
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName('ZGCAI 光谱仪控制软件')
    app.setOrganizationName('ZGCAI')

    # 全局样式 — 蓝白简洁工业风格
    app.setStyleSheet("""
        QMainWindow {
            background-color: #f0f4f8;
        }
        QGroupBox {
            font-weight: bold;
            font-size: 13px;
            border: 1px solid #c8d6e5;
            border-radius: 8px;
            margin-top: 14px;
            padding: 18px 12px 10px 12px;
            background-color: #ffffff;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 14px;
            padding: 0 8px;
            color: #1a73e8;
        }
        QPushButton {
            border: 1px solid #c8d6e5;
            border-radius: 5px;
            padding: 6px 14px;
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #ffffff, stop:1 #f5f7fa);
            color: #333333;
            font-size: 12px;
        }
        QPushButton:hover {
            background: #e8f0fe;
            border-color: #1a73e8;
        }
        QPushButton:pressed {
            background: #d2e3fc;
        }
        QPushButton:disabled {
            background: #f0f0f0;
            color: #999999;
            border-color: #e0e0e0;
        }
        QPushButton:checked {
            background: #1a73e8;
            color: white;
            border-color: #1557b0;
        }
        /* 设备卡片按钮 — 色彩风格按钮 */
        QPushButton[btnRole="start"] {
            background: #1a73e8; color: white; border: none;
            font-weight: bold; font-size: 12px; padding: 5px 14px; border-radius: 4px;
        }
        QPushButton[btnRole="start"]:hover { background: #1557b0; }
        QPushButton[btnRole="stop"] {
            background: #ea4335; color: white; border: none;
            font-weight: bold; font-size: 12px; padding: 5px 14px; border-radius: 4px;
        }
        QPushButton[btnRole="stop"]:hover { background: #d33426; }
        QPushButton[btnRole="enable"] {
            background: #34a853; color: white; border: none;
            font-weight: bold; font-size: 11px; padding: 4px 10px; border-radius: 4px;
        }
        QPushButton[btnRole="enable"]:hover { background: #2d9249; }
        QPushButton[btnRole="disable"] {
            background: #ea4335; color: white; border: none;
            font-weight: bold; font-size: 11px; padding: 4px 10px; border-radius: 4px;
        }
        QPushButton[btnRole="disable"]:hover { background: #d33426; }
        QPushButton[btnRole="masterStart"] {
            background: #1a73e8; color: white; border: 2px solid #1557b0;
            font-weight: bold; font-size: 13px; padding: 8px 16px; border-radius: 6px;
        }
        QPushButton[btnRole="masterStart"]:hover { background: #1557b0; }
        QPushButton[btnRole="masterStop"] {
            background: #ea4335; color: white; border: 2px solid #c62828;
            font-weight: bold; font-size: 13px; padding: 8px 16px; border-radius: 6px;
        }
        QPushButton[btnRole="masterStop"]:hover { background: #d33426; }
        QPushButton[btnRole="close"] {
            font-size: 14px; font-weight: bold; padding: 0; border: none;
            border-radius: 13px; background: #f0f0f0; color: #999;
        }
        QPushButton[btnRole="close"]:hover { background: #ea4335; color: white; }
        QComboBox {
            border: 1px solid #c8d6e5;
            border-radius: 5px;
            padding: 4px 8px;
            background-color: #ffffff;
            font-size: 12px;
        }
        QComboBox:hover {
            border-color: #1a73e8;
        }
        QComboBox::drop-down {
            border: none;
            width: 24px;
        }
        QSpinBox, QDoubleSpinBox {
            border: 1px solid #c8d6e5;
            border-radius: 5px;
            padding: 4px 8px;
            background-color: #ffffff;
            font-size: 12px;
        }
        QSpinBox:hover, QDoubleSpinBox:hover {
            border-color: #1a73e8;
        }
        QLineEdit {
            border: 1px solid #c8d6e5;
            border-radius: 5px;
            padding: 5px 10px;
            background-color: #ffffff;
            font-size: 12px;
        }
        QLineEdit:hover {
            border-color: #1a73e8;
        }
        QListWidget {
            border: 1px solid #c8d6e5;
            border-radius: 6px;
            background-color: #ffffff;
            padding: 4px;
            font-size: 12px;
        }
        QListWidget::item {
            padding: 6px 8px;
            border-radius: 3px;
        }
        QListWidget::item:selected {
            background-color: #e8f0fe;
            color: #1a73e8;
        }
        QListWidget::item:hover {
            background-color: #f0f4f8;
        }
        QCheckBox {
            spacing: 8px;
            color: #333333;
            font-size: 12px;
        }
        QCheckBox::indicator {
            width: 16px;
            height: 16px;
            border: 2px solid #c8d6e5;
            border-radius: 3px;
            background: white;
        }
        QCheckBox::indicator:checked {
            background: #1a73e8;
            border-color: #1a73e8;
        }
        QStatusBar {
            background-color: #e8f0fe;
            border-top: 1px solid #c8d6e5;
            color: #1a73e8;
            font-size: 12px;
            padding: 2px 8px;
        }
        QTabWidget::pane {
            border: 1px solid #c8d6e5;
            border-radius: 6px;
            background-color: #ffffff;
        }
        QTabBar::tab {
            padding: 8px 20px;
            border: 1px solid #c8d6e5;
            border-bottom: none;
            border-radius: 6px 6px 0 0;
            background: #f0f4f8;
            margin-right: 2px;
        }
        QTabBar::tab:selected {
            background: #ffffff;
            border-bottom: 2px solid #1a73e8;
            color: #1a73e8;
        }
        QSplitter::handle {
            background-color: #c8d6e5;
            width: 2px;
        }
        QScrollBar:vertical {
            width: 8px;
            background: #f0f4f8;
            border-radius: 4px;
        }
        QScrollBar::handle:vertical {
            background: #c8d6e5;
            border-radius: 4px;
            min-height: 30px;
        }
        QScrollBar::handle:vertical:hover {
            background: #1a73e8;
        }
        QMenuBar {
            background-color: #ffffff;
            border-bottom: 1px solid #c8d6e5;
            padding: 2px;
        }
        QMenuBar::item {
            padding: 6px 12px;
            border-radius: 4px;
        }
        QMenuBar::item:selected {
            background-color: #e8f0fe;
            color: #1a73e8;
        }
        QMenu {
            background-color: #ffffff;
            border: 1px solid #c8d6e5;
            border-radius: 6px;
            padding: 4px;
        }
        QMenu::item {
            padding: 8px 30px;
            border-radius: 4px;
        }
        QMenu::item:selected {
            background-color: #e8f0fe;
            color: #1a73e8;
        }
        QLabel {
            color: #333333;
            font-size: 12px;
        }
    """)

    # 加载主窗口
    from spectrometer.ui.main_window import MainWindow
    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
