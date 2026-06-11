#!/usr/bin/env bash
# One-shot host setup for the STM32N6 Zephyr project (atlas).
# Run as: sudo bash ~/projects/stm32n6/scripts/host-setup.sh
# Idempotent — safe to re-run.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }

echo "== 1/3 serial access: add ndp to dialout =="
usermod -aG dialout ndp
echo "   (takes effect on next login; 'sg dialout -c <cmd>' works immediately)"

echo "== 2/3 host packages: gperf dtc ccache picocom =="
apt-get install -y -qq gperf device-tree-compiler ccache picocom

echo "== 3/3 udev rules: ST-Link accessible without root (ST official rules) =="
RULES_SRC=/home/ndp/STMicroelectronics/STM32CubeProgrammer/Drivers/rules
if [ -d "$RULES_SRC" ]; then
  cp "$RULES_SRC"/49-stlink*.rules "$RULES_SRC"/50-usb-conf.rules /etc/udev/rules.d/
else
  # fallback if CubeProgrammer not installed yet
  cat > /etc/udev/rules.d/49-stlink.rules <<'EOF'
# STMicroelectronics ST-Link (all variants incl. STLINK-V3EC on NUCLEO-N657X0-Q)
SUBSYSTEM=="usb", ATTRS{idVendor}=="0483", MODE="0666", TAG+="uaccess"
EOF
fi
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --attr-match=idVendor=0483 || true

echo "== done. Re-plug the board once so the udev rule applies. =="
