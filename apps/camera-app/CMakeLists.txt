# SPDX-License-Identifier: Apache-2.0
#
# camera-app — STM32N6 + B-CAMS-IMX as a USB (UVC) webcam.
# Forked from zephyr/samples/subsys/usb/uvc (v4.4.1) and made our own;
# see README.md for what changed.

cmake_minimum_required(VERSION 3.20.0)

# This app targets exactly one hardware combo (NUCLEO-N657X0-Q + B-CAMS-IMX),
# so the shield is baked in here instead of the command line. The board is
# still chosen at build time: 'nucleo_n657x0_q//sb' for the dev RAM-load loop
# (scripts/swd-run.sh), plain 'nucleo_n657x0_q' for flash-boot shipping.
set(SHIELD st_b_cams_imx_mb1854)

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(camera_app LANGUAGES C)

target_sources(app PRIVATE src/main.c src/app_usbd.c)
target_sources_ifdef(CONFIG_APP_FRAME_STATS app PRIVATE src/frame_stats.c)
target_include_directories(app PRIVATE src)
