#!/usr/bin/env python3
#
# QFX5241-64 (Garnet) Platform Initialization Script
#
# Copyright (C) 2026 Juniper Networks
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# Pre-PDDF initialization for QFX5241-64 (Garnet) platform.
#
# Responsibilities:
#   - Set EFI BootNext so warm/cold reboot returns to SONiC
#   - Copy ONIE EEPROM cache for platform EEPROM plugin
#   - Link the platform-root platform.json / default_sku / media_settings.json
#     to the OD or QD HWSKU folder (sonic-cfggen only ever reads the root copy)
#
# NOT done here (handled by pddf_util.py install via pddf-device.json):
#   - Driver loading  -> custom_kos / std_kos / pddf_kos in pddf-device.json
#   - I2C bus fixup   -> drivers assign static bus numbers; no runtime patch needed
#   - Device instantiation -> pddf_util.py
#
# I2C bus number scheme (static, guaranteed by kernel drivers):
#   iSMT (PCIe 00:1f.4)        : i2c-0
#   MUX1 @0x78 ch0-7           : 0x10-0x17
#   MUX2 @0x70 ch0-7           : 0x20-0x27
#   MUX3 @0x76 ch0-7           : 0x30-0x37
#   Garnet ocores CPLD1 port 0-31 : 0x40-0x5f
#   Garnet ocores CPLD2 port 32-63: 0x60-0x7f
#   Garnet ocores SFP   port 64-65: 0x80-0x81

import re
import subprocess
import sys
import time
from sonic_py_common.logger import Logger

SYSLOG_IDENTIFIER = "qfx5241-platform-init"

PLATFORM = "x86_64-juniper_qfx5241-r0"
HWSKU_OD = "Juniper-QFX5241-64-OD"
HWSKU_QD = "Juniper-QFX5241-64-QD"
DMI_PRODUCT_PATH = "/sys/class/dmi/id/product_name"
DMI_OD = "0F5C"
DMI_QD = "0F5D"
SYSEEPROM_CACHE = "/var/run/platform_cache/onie_syseeprom.cache"
TLV_HEADER_LEN = 11
TLV_PRODUCT_NAME = 0x21
QD_PRODUCT_NAME = re.compile(r"(?<![A-Za-z])QD(?![A-Za-z])", re.IGNORECASE)
CACHE_WAIT_SECONDS = 60


def log_info(msg):
    Logger(SYSLOG_IDENTIFIER).log_info(msg)


def log_warning(msg):
    Logger(SYSLOG_IDENTIFIER).log_warning(msg)


def log_error(msg):
    Logger(SYSLOG_IDENTIFIER).log_error(msg)



def remove_stale_pmon_container():
    """Remove stale pmon container so pmon.sh creates a fresh one on next start.

    After a reboot, an old pmon container may persist with stale device mounts
    (e.g. /dev/nvme1n1pN) that no longer exist, causing 'docker start' to fail.
    Removing it here (before pmon.service starts) forces pmon.sh to recreate it.
    """
    result = subprocess.run(['docker', 'rm', '-f', 'pmon'],
                            capture_output=True, text=True)
    if result.returncode == 0:
        log_info("Removed stale pmon container")


def read_product_name(path=SYSEEPROM_CACHE):
    """Return the Product Name TLV from the ONIE syseeprom cache, or None."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if len(data) < TLV_HEADER_LEN or data[:8] != b"TlvInfo\x00":
        return None
    end = min(len(data), TLV_HEADER_LEN + int.from_bytes(data[9:11], "big"))
    pos = TLV_HEADER_LEN
    while pos + 2 <= end:
        code, length = data[pos], data[pos + 1]
        if code == TLV_PRODUCT_NAME:
            return data[pos + 2:pos + 2 + length].decode(
                "ascii", "replace").strip("\x00 ")
        pos += 2 + length
    return None


def detect_hwsku(product_name):
    return HWSKU_QD if QD_PRODUCT_NAME.search(product_name) else HWSKU_OD


def detect_dmi_hwsku(path=DMI_PRODUCT_PATH):
    """Return the HWSKU encoded by the DMI system Product Name, or None."""
    try:
        with open(path) as f:
            product = f.read().strip().upper()
    except OSError as e:
        log_warning("Cannot read DMI Product Name from {}: {}".format(path, e))
        return None
    if product == DMI_OD:
        return HWSKU_OD
    if product == DMI_QD:
        return HWSKU_QD
    log_warning("Unknown DMI Product Name '{}'; trying EEPROM".format(product))
    return None


def select_sku(wait=0, cache=SYSEEPROM_CACHE, dmi_path=DMI_PRODUCT_PATH):
    """Link the platform-root SKU files to the detected HWSKU.

    Return the selected HWSKU, or None when detection/linking is unavailable.
    """
    hwsku = detect_dmi_hwsku(dmi_path)
    source = "DMI Product Name"
    if hwsku is None:
        deadline = time.monotonic() + wait
        product_name = read_product_name(cache)
        while product_name is None and time.monotonic() < deadline:
            time.sleep(1)
            product_name = read_product_name(cache)
        if product_name is None:
            log_warning(
                "No Product Name in {}; SKU links unchanged".format(cache))
            return None
        hwsku = detect_hwsku(product_name)
        source = "EEPROM Product Name '{}'".format(product_name)

    try:
        from sonic_platform.utils import setup_sku_symlinks
        linked = setup_sku_symlinks(PLATFORM, hwsku)
    except ImportError as e:
        log_error("Cannot link SKU files: {}".format(e))
        return None
    if not linked:
        log_error("Could not link SKU files for {}".format(hwsku))
        return None
    log_info("{}: selected {}".format(source, hwsku))
    return hwsku


def setup():
    try:
        select_sku()
    except Exception as e:
        log_error("SKU selection failed: {}".format(e))
    remove_stale_pmon_container()


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'sku-symlinks':
        select_sku(wait=CACHE_WAIT_SECONDS)
    else:
        setup()
