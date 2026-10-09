#!/usr/bin/env bash
# Run locally with sudo; installs signed upstream APT packages only.
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
  echo 'Run: sudo bash scripts/install-host-ubuntu.sh' >&2
  exit 1
fi
source /etc/os-release
if [[ ${ID:-} != ubuntu || ${VERSION_CODENAME:-} != noble || $(dpkg --print-architecture) != amd64 ]]; then
  echo 'This installer targets Ubuntu 24.04 amd64 only.' >&2
  exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg build-essential dkms "linux-headers-$(uname -r)"
install -d -m 0755 /etc/apt/keyrings
lab_tmp=$(mktemp -d)
trap 'rm -rf -- "$lab_tmp"' EXIT
curl --fail --show-error --location --proto '=https' --tlsv1.2 \
  https://www.virtualbox.org/download/oracle_vbox_2016.asc -o "$lab_tmp/oracle.asc"
curl --fail --show-error --location --proto '=https' --tlsv1.2 \
  https://apt.releases.hashicorp.com/gpg -o "$lab_tmp/hashicorp.asc"
gpg --batch --yes --dearmor --output "$lab_tmp/oracle.gpg" "$lab_tmp/oracle.asc"
gpg --batch --yes --dearmor --output "$lab_tmp/hashicorp.gpg" "$lab_tmp/hashicorp.asc"
install -m 0644 "$lab_tmp/oracle.gpg" /etc/apt/keyrings/agentic-lab-oracle.gpg
install -m 0644 "$lab_tmp/hashicorp.gpg" /etc/apt/keyrings/agentic-lab-hashicorp.gpg
printf '%s\n' 'deb [arch=amd64 signed-by=/etc/apt/keyrings/agentic-lab-oracle.gpg] https://download.virtualbox.org/virtualbox/debian noble contrib' > /etc/apt/sources.list.d/agentic-lab-virtualbox.list
printf '%s\n' 'deb [arch=amd64 signed-by=/etc/apt/keyrings/agentic-lab-hashicorp.gpg] https://apt.releases.hashicorp.com noble main' > /etc/apt/sources.list.d/agentic-lab-hashicorp.list
apt-get update
apt-get install -y virtualbox-7.2 vagrant
modprobe vboxdrv
VBoxManage --version
vagrant --version
if ! grep -qw vmx /proc/cpuinfo; then
  echo 'Installed, but Intel VT-x is not exposed to Linux. Enable Intel Virtualization Technology in BIOS/UEFI before starting the VM.' >&2
  exit 2
fi
