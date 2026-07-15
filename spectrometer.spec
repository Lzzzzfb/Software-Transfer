# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for ZGCAI 光谱仪上位机控制软件
"""

import os
import sys

# SPECPATH: PyInstaller 内置变量, 指向 spec 文件所在目录
_spec_dir = SPECPATH

# 将项目目录加入 sys.path 以便导入 spectrometer
sys.path.insert(0, _spec_dir)

# ---- 查找 PyQt5 路径 ----
import PyQt5
_qt5_dir = os.path.join(os.path.dirname(PyQt5.__file__), 'Qt5')
from PyInstaller.utils.hooks import qt

# 修正 Qt 库路径(避免中文路径被 Qt 返回为 ???)
_qt5_prefix = _qt5_dir
qt_info = qt.pyqt5_library_info
_ = qt_info.location
qt_info.location['PrefixPath'] = _qt5_prefix
qt_info.location['PluginsPath'] = os.path.join(_qt5_prefix, 'plugins')
qt_info.location['LibrariesPath'] = os.path.join(_qt5_prefix, 'lib')
qt_info.location['BinariesPath'] = os.path.join(_qt5_prefix, 'bin')

# ---- 收集 Qt 平台插件和数据文件 ----
datas = []
_platforms_dir = os.path.join(_qt5_dir, 'plugins', 'platforms')
if os.path.isdir(_platforms_dir):
    datas.append((_platforms_dir, 'PyQt5/Qt5/plugins/platforms'))

for _plug in ['styles', 'imageformats', 'iconengines']:
    _p = os.path.join(_qt5_dir, 'plugins', _plug)
    if os.path.isdir(_p):
        datas.append((_p, f'PyQt5/Qt5/plugins/{_plug}'))

# Qt bin 目录中的 DLL
_bin_dir = os.path.join(_qt5_dir, 'bin')
if os.path.isdir(_bin_dir):
    for _f in os.listdir(_bin_dir):
        if _f.endswith('.dll'):
            datas.append((os.path.join(_bin_dir, _f), 'PyQt5/Qt5/bin'))

# qt.conf
_qt_conf = os.path.join(os.path.dirname(PyQt5.__file__), 'qt.conf')
if os.path.exists(_qt_conf):
    datas.append((_qt_conf, 'PyQt5'))

# ---- 隐藏导入 ----
hiddenimports = [
    'PyQt5', 'PyQt5.QtCore', 'PyQt5.QtGui', 'PyQt5.QtWidgets',
    'PyQt5.QtSerialPort', 'PyQt5.sip',
    'pyqtgraph', 'pyqtgraph.graphicsItems', 'pyqtgraph.widgets',
    'numpy', 'numpy.core', 'numpy.linalg', 'numpy.random', 'numpy.fft',
    'numpy._core', 'numpy._core.multiarray', 'numpy._core.umath',
    'serial', 'serial.tools', 'serial.tools.list_ports',
]

# 自动收集 spectrometer 包下所有模块
import spectrometer
_sp_path = os.path.dirname(spectrometer.__file__)
for _root, _dirs, _files in os.walk(_sp_path):
    for _f in _files:
        if _f.endswith('.py') and _f != '__init__.py':
            _rel = os.path.relpath(os.path.join(_root, _f), _sp_path)
            _mod = 'spectrometer.' + _rel.replace(os.sep, '.')[:-3]
            hiddenimports.append(_mod)
    for _d in _dirs:
        _rel = os.path.relpath(os.path.join(_root, _d), _sp_path).replace(os.sep, '.')
        if _rel != '.':
            hiddenimports.append('spectrometer.' + _rel)

a = Analysis(
    [os.path.join(_spec_dir, 'main.py')],
    pathex=[_spec_dir],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ZGCAI_Spectrometer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
