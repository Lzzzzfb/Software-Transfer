#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/zgcai-spectrometer"
UDEV_RULE="/etc/udev/rules.d/99-zgcai-spectrometer.rules"
APP_ENTRY="/usr/share/applications/zgcai-spectrometer.desktop"
TARGET_USER="${SUDO_USER:-${USER}}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 sudo 运行: sudo ./install.sh" >&2
  exit 1
fi

if [[ "$(dpkg --print-architecture)" != "arm64" ]]; then
  echo "仅支持 Debian ARM64，当前架构: $(dpkg --print-architecture)" >&2
  exit 1
fi

source /etc/os-release
if [[ "${ID:-}" != "debian" || "${VERSION_ID:-}" != "12" ]]; then
  echo "仅支持 Debian 12 Bookworm，当前系统: ${PRETTY_NAME:-未知}" >&2
  exit 1
fi

if [[ "$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" != "3.11" ]]; then
  echo "需要 Python 3.11" >&2
  exit 1
fi

AVAILABLE_KB="$(df -Pk /opt | awk 'NR==2 {print $4}')"
if (( AVAILABLE_KB < 2097152 )); then
  echo "/opt 至少需要 2 GiB 可用空间" >&2
  exit 1
fi

apt-get update
apt-get install -y \
  python3 python3-venv python3-pip \
  python3-pyqt6 python3-pyqt6.qtserialport \
  libgl1 libegl1 libxcb-cursor0 libxkbcommon-x11-0 \
  fonts-noto-cjk

install -d -m 0755 "${INSTALL_DIR}"
cp -a "${SCRIPT_DIR}/app/." "${INSTALL_DIR}/"
install -m 0755 "${SCRIPT_DIR}/run.sh" "${INSTALL_DIR}/run.sh"
install -m 0644 "${SCRIPT_DIR}/requirements-rock4bplus.txt" \
  "${INSTALL_DIR}/requirements-rock4bplus.txt"

python3 -m venv --system-site-packages "${INSTALL_DIR}/.venv"
"${INSTALL_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${INSTALL_DIR}/.venv/bin/python" -m pip install \
  -r "${INSTALL_DIR}/requirements-rock4bplus.txt"

install -m 0644 "${SCRIPT_DIR}/99-zgcai-spectrometer.rules" "${UDEV_RULE}"
udevadm control --reload-rules
udevadm trigger --subsystem-match=tty

if getent passwd "${TARGET_USER}" >/dev/null; then
  usermod -a -G dialout "${TARGET_USER}"
else
  echo "无法确认桌面用户 ${TARGET_USER}" >&2
  exit 1
fi
TARGET_GROUP="$(id -gn "${TARGET_USER}")"

"${INSTALL_DIR}/.venv/bin/python" -c \
  "from PyQt6 import QtCore, QtWidgets, QtSerialPort; import numpy, scipy, xlsxwriter, openpyxl; print('依赖自检通过', QtCore.QT_VERSION_STR)"

install -m 0644 "${SCRIPT_DIR}/zgcai-spectrometer.desktop" "${APP_ENTRY}"

TARGET_HOME="$(getent passwd "${TARGET_USER}" | cut -d: -f6)"
DESKTOP_DIR="${TARGET_HOME}/Desktop"
if [[ -d "${TARGET_HOME}/桌面" ]]; then
  DESKTOP_DIR="${TARGET_HOME}/桌面"
fi
if [[ -d "${DESKTOP_DIR}" ]]; then
  install -o "${TARGET_USER}" -g "${TARGET_GROUP}" -m 0755 \
    "${SCRIPT_DIR}/zgcai-spectrometer.desktop" \
    "${DESKTOP_DIR}/zgcai-spectrometer.desktop"
fi

echo "安装完成。请注销并重新登录，然后重新插拔光谱仪。"
echo "可从应用菜单或 ${INSTALL_DIR}/run.sh 手动启动。"
