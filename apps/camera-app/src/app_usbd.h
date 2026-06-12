/*
 * USB device setup for camera-app.
 *
 * Forked from zephyr/samples/subsys/usb/common/sample_usbd.h (v4.4.1) —
 * the in-tree helper is sample-scoped and must not be referenced by
 * applications, so we own a copy.
 *
 * Copyright (c) 2023 Nordic Semiconductor ASA.
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef APP_USBD_H
#define APP_USBD_H

#include <zephyr/usb/usbd.h>

/*
 * Configure and initialize the USB device from the APP_USBD_* Kconfig
 * options: device context, string descriptors, configuration, all available
 * class instances. Returns the initialized context, or NULL on failure.
 */
struct usbd_context *app_usbd_init_device(usbd_msg_cb_t msg_cb);

/*
 * Same, but stops short of usbd_init() so the caller can add descriptors or
 * features first.
 */
struct usbd_context *app_usbd_setup_device(usbd_msg_cb_t msg_cb);

#endif /* APP_USBD_H */
