#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/venv"
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

if [[ -f "${VENV_DIR}/bin/activate" ]]; then
  # Unix-like (Linux, macOS)
  # shellcheck disable=SC1091
  source "${VENV_DIR}/bin/activate"
elif [[ -f "${VENV_DIR}/Scripts/activate" ]]; then
  # Windows (Git Bash, MSYS2)
  # shellcheck disable=SC1091
  source "${VENV_DIR}/Scripts/activate"
else
  log "Ошибка: не найден скрипт активации виртуального окружения в ${VENV_DIR}!" >&2
  exit 1
fi

log "Обновляю pip в виртуальном окружении..."
python -m pip install --upgrade pip

log "Устанавливаю зависимости из ${REQ_FILE}..."
python -m pip install -r "${REQ_FILE}"

if command -v microsoft-edge >/dev/null 2>&1 || command -v microsoft-edge-stable >/dev/null 2>&1 || dpkg -s microsoft-edge-stable >/dev/null 2>&1; then
  log "Microsoft Edge уже установлен."
else
  log "Устанавливаю Microsoft Edge..."
  ${SUDO} apt-get update
  ${SUDO} apt-get install -y software-properties-common apt-transport-https wget
  wget -q https://packages.microsoft.com/keys/microsoft.asc -O- | ${SUDO} apt-key add -
  ${SUDO} add-apt-repository "deb [arch=amd64] https://packages.microsoft.com/repos/edge stable main"
  ${SUDO} apt-get update
  ${SUDO} apt-get install -y microsoft-edge-stable

  log "Microsoft Edge установлен."
fi

log "Готово. Виртуальное окружение и зависимости настроены."
log "Для запуска скрипта используйте команду:"
log "python3 ${SCRIPT_DIR}/chipdip/main_detsel.py"
