# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('C:\\Users\\admin\\Desktop\\光谱仪\\.venv\\Lib\\site-packages\\PyQt5\\Qt5\\bin\\Qt5SerialPort.dll', '.'), ('C:\\Users\\admin\\Desktop\\光谱仪\\.venv\\Lib\\site-packages\\PyQt5\\Qt5\\plugins\\platforms', 'platforms'), ('C:\\Users\\admin\\Desktop\\光谱仪\\.venv\\Lib\\site-packages\\PyQt5\\Qt5\\plugins\\styles', 'styles')],
    hiddenimports=['PyQt5.QtSerialPort', 'PyQt5.QtWidgets', 'PyQt5.QtCore', 'PyQt5.QtGui', 'pyqtgraph', 'numpy.core._methods', 'numpy.lib.format', 'serial.tools.list_ports_windows'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'pandas', 'PyQt5.QtBluetooth', 'PyQt5.QtDBus', 'PyQt5.QtDesigner', 'PyQt5.QtHelp', 'PyQt5.QtLocation', 'PyQt5.QtMultimedia', 'PyQt5.QtMultimediaWidgets', 'PyQt5.QtNfc', 'PyQt5.QtPositioning', 'PyQt5.QtQml', 'PyQt5.QtQuick', 'PyQt5.QtQuickWidgets', 'PyQt5.QtRemoteObjects', 'PyQt5.QtSensors', 'PyQt5.QtSql', 'PyQt5.QtSvg', 'PyQt5.QtTest', 'PyQt5.QtTextToSpeech', 'PyQt5.QtWebChannel', 'PyQt5.QtWebSockets', 'PyQt5.QtWebView', 'PyQt5.QtWinExtras', 'PyQt5.QtXml', 'PyQt5.QtXmlPatterns', 'PyQt5.QtOpenGL', 'PyQt5.QtQuick3D', 'PyQt5.QtQuickControls2', 'PyQt5.QtQuickTemplates2'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ZGCAI光谱仪控制软件',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ZGCAI光谱仪控制软件',
)
