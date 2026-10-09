#!/usr/bin/env bash
# Shared by fresh provisioning and the isolated prepared-box builder.

# Выполняет один этап и печатает его длительность в журнал панели.
lab_stage() {
  local label=$1 started=$SECONDS
  shift
  printf '[lab] %s: starting\n' "$label"
  "$@"
  printf '[lab] %s: %ss\n' "$label" "$((SECONDS - started))"
}

# Устанавливает только недостающие зависимости ВНУТРИ гостевой VM.
# profile=all используется при подготовке box: ему нужны пакеты всех профилей.
# Старое ядро базового box может исчезнуть из APT, поэтому builder устанавливает
# актуальную пару kernel/headers и затем перезагружается перед упаковкой.
# В обычном прогоне headers должны соответствовать уже работающему ядру.
lab_dependencies() {
  local profile=$1
  local packages=()
  export DEBIAN_FRONTEND=noninteractive
  if ! command -v docker >/dev/null || ! docker compose version >/dev/null; then
    packages+=(docker.io docker-compose-v2)
  fi
  command -v openssl >/dev/null || packages+=(openssl)
  if [[ $profile == all || $profile == hostpath-etc ]]; then
    command -v cron >/dev/null || packages+=(cron)
  fi
  if [[ $profile == all || $profile == sys-module ]]; then
    if [[ ! -d /lib/modules/$(uname -r)/build ]]; then
      if [[ $profile == all ]]; then
        # Old Bento kernels can have vanished from current APT indexes.
        # The builder reboots into this matching kernel/header pair.
        packages+=(linux-image-generic linux-headers-generic)
      else
        packages+=("linux-headers-$(uname -r)")
      fi
    fi
    if ! command -v gcc >/dev/null || ! command -v make >/dev/null; then
      packages+=(build-essential)
    fi
  fi
  if ((${#packages[@]})); then
    apt-get update
    apt-get install -y "${packages[@]}"
  else
    echo '[lab] dependencies already installed; skipping APT'
  fi
  systemctl enable --now docker
  if [[ $profile == all || $profile == hostpath-etc ]]; then
    systemctl enable --now cron
  fi
}

# Выбирает image по хешу Dockerfile, adapter.py и версии OpenClaw.
# Совпадение позволяет пропустить дорогую сборку в VM из подготовленного box.
# При изменении входных файлов создаётся новый tag; Compose получает его через
# LAB_ESCAPE_IMAGE. Чистоту гостевого Docker обеспечивает пересоздание VM в up.
lab_image() {
  local fingerprint
  fingerprint=$(
    { printf '%s\n' "${LAB_OPENCLAW_VERSION:-2026.9.6}";
      cat /workspace/vm/escape/Dockerfile /workspace/vm/openclaw-adapter/adapter.py;
    } | sha256sum
  )
  export LAB_ESCAPE_IMAGE="agentic-lab-escape:${fingerprint%% *}"
  if docker image inspect "$LAB_ESCAPE_IMAGE" >/dev/null 2>&1; then
    printf '[lab] reusing prepared image %s\n' "$LAB_ESCAPE_IMAGE"
  else
    printf '[lab] building image %s\n' "$LAB_ESCAPE_IMAGE"
    docker compose -f vm/escape/compose.yml build sandbox
  fi
}
