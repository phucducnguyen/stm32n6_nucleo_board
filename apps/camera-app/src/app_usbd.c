/*
 * USB device setup for camera-app.
 *
 * Forked from zephyr/samples/subsys/usb/common/sample_usbd_init.c (v4.4.1),
 * renamed SAMPLE_USBD_* -> APP_USBD_* and trimmed of the BOS/USB 2.0
 * extension descriptor example code.
 *
 * Copyright (c) 2023 Nordic Semiconductor ASA
 * SPDX-License-Identifier: Apache-2.0
 */

#include <stdint.h>

#include <zephyr/device.h>
#include <zephyr/usb/usbd.h>

#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(app_usbd);

/* By default, do not register the USB DFU class DFU mode instance. */
static const char *const blocklist[] = {
	"dfu_dfu",
	NULL,
};

USBD_DEVICE_DEFINE(app_usbd,
		   DEVICE_DT_GET(DT_NODELABEL(zephyr_udc0)),
		   CONFIG_APP_USBD_VID, CONFIG_APP_USBD_PID);

USBD_DESC_LANG_DEFINE(app_lang);
USBD_DESC_MANUFACTURER_DEFINE(app_mfr, CONFIG_APP_USBD_MANUFACTURER);
USBD_DESC_PRODUCT_DEFINE(app_product, CONFIG_APP_USBD_PRODUCT);
IF_ENABLED(CONFIG_HWINFO, (USBD_DESC_SERIAL_NUMBER_DEFINE(app_sn)));

USBD_DESC_CONFIG_DEFINE(fs_cfg_desc, "FS Configuration");
USBD_DESC_CONFIG_DEFINE(hs_cfg_desc, "HS Configuration");

static const uint8_t attributes = (IS_ENABLED(CONFIG_APP_USBD_SELF_POWERED) ?
				   USB_SCD_SELF_POWERED : 0) |
				  (IS_ENABLED(CONFIG_APP_USBD_REMOTE_WAKEUP) ?
				   USB_SCD_REMOTE_WAKEUP : 0);

USBD_CONFIGURATION_DEFINE(app_fs_config,
			  attributes,
			  CONFIG_APP_USBD_MAX_POWER, &fs_cfg_desc);

USBD_CONFIGURATION_DEFINE(app_hs_config,
			  attributes,
			  CONFIG_APP_USBD_MAX_POWER, &hs_cfg_desc);

static void app_fix_code_triple(struct usbd_context *uds_ctx,
				const enum usbd_speed speed)
{
	/* Always use class code information from Interface Descriptors */
	if (IS_ENABLED(CONFIG_USBD_CDC_ACM_CLASS) ||
	    IS_ENABLED(CONFIG_USBD_CDC_ECM_CLASS) ||
	    IS_ENABLED(CONFIG_USBD_CDC_NCM_CLASS) ||
	    IS_ENABLED(CONFIG_USBD_MIDI2_CLASS) ||
	    IS_ENABLED(CONFIG_USBD_AUDIO2_CLASS) ||
	    IS_ENABLED(CONFIG_USBD_VIDEO_CLASS)) {
		/*
		 * Class with multiple interfaces have an Interface
		 * Association Descriptor available, use an appropriate triple
		 * to indicate it.
		 */
		usbd_device_set_code_triple(uds_ctx, speed,
					    USB_BCC_MISCELLANEOUS, 0x02, 0x01);
	} else {
		usbd_device_set_code_triple(uds_ctx, speed, 0, 0, 0);
	}
}

struct usbd_context *app_usbd_setup_device(usbd_msg_cb_t msg_cb)
{
	int err;

	err = usbd_add_descriptor(&app_usbd, &app_lang);
	if (err) {
		LOG_ERR("Failed to initialize language descriptor (%d)", err);
		return NULL;
	}

	err = usbd_add_descriptor(&app_usbd, &app_mfr);
	if (err) {
		LOG_ERR("Failed to initialize manufacturer descriptor (%d)", err);
		return NULL;
	}

	err = usbd_add_descriptor(&app_usbd, &app_product);
	if (err) {
		LOG_ERR("Failed to initialize product descriptor (%d)", err);
		return NULL;
	}

	IF_ENABLED(CONFIG_HWINFO, (
		err = usbd_add_descriptor(&app_usbd, &app_sn);
	))
	if (err) {
		LOG_ERR("Failed to initialize SN descriptor (%d)", err);
		return NULL;
	}

	if (USBD_SUPPORTS_HIGH_SPEED &&
	    usbd_caps_speed(&app_usbd) == USBD_SPEED_HS) {
		err = usbd_add_configuration(&app_usbd, USBD_SPEED_HS,
					     &app_hs_config);
		if (err) {
			LOG_ERR("Failed to add High-Speed configuration");
			return NULL;
		}

		err = usbd_register_all_classes(&app_usbd, USBD_SPEED_HS, 1,
						blocklist);
		if (err) {
			LOG_ERR("Failed to add register classes");
			return NULL;
		}

		app_fix_code_triple(&app_usbd, USBD_SPEED_HS);
	}

	err = usbd_add_configuration(&app_usbd, USBD_SPEED_FS,
				     &app_fs_config);
	if (err) {
		LOG_ERR("Failed to add Full-Speed configuration");
		return NULL;
	}

	err = usbd_register_all_classes(&app_usbd, USBD_SPEED_FS, 1, blocklist);
	if (err) {
		LOG_ERR("Failed to add register classes");
		return NULL;
	}

	app_fix_code_triple(&app_usbd, USBD_SPEED_FS);
	usbd_self_powered(&app_usbd, attributes & USB_SCD_SELF_POWERED);

	if (msg_cb != NULL) {
		err = usbd_msg_register_cb(&app_usbd, msg_cb);
		if (err) {
			LOG_ERR("Failed to register message callback");
			return NULL;
		}
	}

	return &app_usbd;
}

struct usbd_context *app_usbd_init_device(usbd_msg_cb_t msg_cb)
{
	int err;

	if (app_usbd_setup_device(msg_cb) == NULL) {
		return NULL;
	}

	err = usbd_init(&app_usbd);
	if (err) {
		LOG_ERR("Failed to initialize device support");
		return NULL;
	}

	return &app_usbd;
}
