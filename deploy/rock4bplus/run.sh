#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/zgcai-spectrometer"
STATE_HOME="${XDG_STATE_HOME:-${HOME}/.local/state}"
LOG_DIR="${STATE_HOME}/ZGCAI/Spectrometer"
DATA_DIR="${HOME}/ZGCAI-Spectrometer-Data"

mkdir -p "${LOG_DIR}" "${DATA_DIR}"
cd "${INSTALL_DIR}"
exec "${INSTALL_DIR}/.venv/bin/python" "${INSTALL_DIR}/main.py" "$@" \
  2>>"${LOG_DIR}/startup.log"
