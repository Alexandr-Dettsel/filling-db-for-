#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
REQ_FILE="${SCRIPT_DIR}/requirements.txt"

log() {
  echo "[setup] $*"
}

if command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
else
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Нужны root-права: запустите скрипт от root или установите sudo." >&2
    exit 1
  fi
  SUDO=""
fi

if ! command -v python3 >/dev/null 2>&1; then
  log "Устанавливаю python3..."
  ${SUDO} apt-get update
  ${SUDO} apt-get install -y python3
fi

if ! dpkg -s python3-venv >/dev/null 2>&1; then
  log "Устанавливаю python3-venv..."
  ${SUDO} apt-get update
  ${SUDO} apt-get install -y python3-venv
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  log "Создаю виртуальное окружение: ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
else
  log "Виртуальное окружение уже существует: ${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

log "Обновляю pip в виртуальном окружении..."
python -m pip install --upgrade pip

log "Устанавливаю зависимости из ${REQ_FILE}..."
python -m pip install -r "${REQ_FILE}"

if command -v microsoft-edge >/dev/null 2>&1 || dpkg -s microsoft-edge-stable >/dev/null 2>&1; then
  log "Microsoft Edge уже установлен."
else
  log "Устанавливаю Microsoft Edge..."
  ${SUDO} apt-get update
  ${SUDO} apt-get install -y software-properties-common apt-transport-https wget gnupg

  if [[ ! -f /etc/apt/trusted.gpg.d/microsoft.gpg ]]; then
    wget -q https://packages.microsoft.com/keys/microsoft.asc -O- | ${SUDO} apt-key add -
  fi

  if ! grep -Rqs "packages.microsoft.com/repos/edge" /etc/apt/sources.list /etc/apt/sources.list.d/*.list 2>/dev/null; then
    ${SUDO} add-apt-repository -y "deb [arch=amd64] https://packages.microsoft.com/repos/edge stable main"
  fi

  ${SUDO} apt-get update
  ${SUDO} apt-get install -y microsoft-edge-stable
  log "Microsoft Edge установлен."
fi

log "Готово. Виртуальное окружение и зависимости настроены."