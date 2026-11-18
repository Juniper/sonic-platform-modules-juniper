"""Unit tests for qfx5241 platform SKU selection."""

import functools
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.conftest import (BASE_DIR, stub_sonic_py_common, stub_syslog,
                            load_module)

stub_syslog()
stub_sonic_py_common()

mod = load_module("qfx5241_platform_init",
                  BASE_DIR / "qfx5241" / "utils" / "qfx5241_platform_init.py")
utils = load_module("juniper_sonic_platform_utils",
                    BASE_DIR / "sonic_platform" / "utils.py")

OD, QD = mod.HWSKU_OD, mod.HWSKU_QD


def tlv_cache(product_name=None, before=b"", after=b""):
    body = before
    if product_name is not None:
        value = product_name.encode()
        body += bytes([0x21, len(value)]) + value
    body += after
    return b"TlvInfo\x00" + b"\x01" + len(body).to_bytes(2, "big") + body


class TestReadProductName(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "onie_syseeprom.cache"

    def test_reads_product_name_after_other_tlvs(self):
        self.path.write_bytes(
            tlv_cache("QFX5241-64QD", before=bytes([0x22, 3]) + b"abc"))
        self.assertEqual(mod.read_product_name(str(self.path)), "QFX5241-64QD")

    def test_missing_cache_is_none(self):
        self.assertIsNone(mod.read_product_name(str(self.path)))

    def test_bad_header_is_none(self):
        self.path.write_bytes(b"NotTlv\x00\x00" + b"\x00" * 20)
        self.assertIsNone(mod.read_product_name(str(self.path)))

    def test_truncated_cache_is_none(self):
        self.path.write_bytes(tlv_cache("QFX5241")[:12])
        self.assertIsNone(mod.read_product_name(str(self.path)))

    def test_no_product_name_tlv_is_none(self):
        self.path.write_bytes(
            tlv_cache(None, after=bytes([0x22, 3]) + b"abc"))
        self.assertIsNone(mod.read_product_name(str(self.path)))

    def test_ignores_data_past_the_total_length(self):
        data = tlv_cache(None) + bytes([0x21, 4]) + b"QD64"
        self.path.write_bytes(data)
        self.assertIsNone(mod.read_product_name(str(self.path)))


class TestDetectHwsku(unittest.TestCase):

    def test_od_names(self):
        for name in ("QFX5241", "QFX5241-64OD", "QFX5241-64-OD"):
            self.assertEqual(mod.detect_hwsku(name), OD, name)

    def test_qd_names(self):
        for name in ("QFX5241-64QD", "QFX5241-64-QD",
                     "qfx5241-64qd", "QFX5241 QD"):
            self.assertEqual(mod.detect_hwsku(name), QD, name)

    def test_qd_must_be_a_standalone_token(self):
        self.assertEqual(mod.detect_hwsku("QFX5241-AQDB"), OD)


class TestDetectDmiHwsku(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "product_name"
        for name in ("log_warning",):
            patcher = mock.patch.object(mod, name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_detects_od_and_qd_ids(self):
        for value, expected in (("0F5C", OD), ("0F5D", QD),
                                (" 0f5d\n", QD)):
            self.path.write_text(value)
            self.assertEqual(
                mod.detect_dmi_hwsku(str(self.path)), expected, value)

    def test_unknown_and_missing_dmi_are_none(self):
        self.path.write_text("0F1C")
        self.assertIsNone(mod.detect_dmi_hwsku(str(self.path)))
        self.path.unlink()
        self.assertIsNone(mod.detect_dmi_hwsku(str(self.path)))


class TestSelectSku(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.device = Path(tmp.name) / "device"
        self.platform_dir = self.device / mod.PLATFORM
        for sku in (OD, QD):
            directory = self.platform_dir / sku
            directory.mkdir(parents=True)
            (directory / "platform.json").write_text('{"sku": "%s"}' % sku)
            (directory / "media_settings.json").write_text('{"sku": "%s"}' % sku)
            (directory / "hwsku_name").write_text("%s l3\n" % sku)
        (self.platform_dir / "platform.json").write_text('{"sku": "%s"}' % OD)
        (self.platform_dir / "media_settings.json").write_text(
            '{"sku": "%s"}' % OD)
        (self.platform_dir / "default_sku").write_text("%s l3\n" % OD)
        self.cache = Path(tmp.name) / "onie_syseeprom.cache"
        self.dmi = Path(tmp.name) / "product_name"

        linker = types.SimpleNamespace(
            setup_sku_symlinks=functools.partial(
                utils.setup_sku_symlinks, base_dir=str(self.device)))
        patcher = mock.patch.dict(
            "sys.modules",
            {"sonic_platform": types.ModuleType("sonic_platform"),
             "sonic_platform.utils": linker})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("log_info", "log_warning", "log_error"):
            patcher = mock.patch.object(mod, name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _select(self, product_name, **kwargs):
        self.cache.write_bytes(tlv_cache(product_name))
        return mod.select_sku(
            cache=str(self.cache), dmi_path=str(self.dmi), **kwargs)

    def _root(self, name):
        return self.platform_dir / name

    def test_qd_board_links_root_files_to_qd(self):
        self.assertEqual(self._select("QFX5241-64QD"), QD)
        self.assertEqual(
            self._root("platform.json").read_text(), '{"sku": "%s"}' % QD)
        self.assertEqual(
            self._root("media_settings.json").read_text(), '{"sku": "%s"}' % QD)
        self.assertEqual(
            self._root("default_sku").read_text(), "%s l3\n" % QD)

    def test_od_board_links_root_files_to_od(self):
        self.dmi.write_text("0F5C")
        self.assertEqual(self._select("QFX5241-64QD"), OD)
        self.assertEqual(
            self._root("platform.json").read_text(), '{"sku": "%s"}' % OD)

    def test_dmi_qd_overrides_eeprom_od(self):
        self.dmi.write_text("0F5D")
        self.assertEqual(self._select("QFX5241-64OD"), QD)

    def test_unknown_dmi_falls_back_to_eeprom(self):
        self.dmi.write_text("0F1C")
        self.assertEqual(self._select("QFX5241-64QD"), QD)

    def test_dmi_detection_does_not_wait_for_eeprom(self):
        self.dmi.write_text("0F5D")
        with mock.patch.object(mod.time, "sleep") as sleep:
            self.assertEqual(
                mod.select_sku(
                    wait=30, cache=str(self.cache), dmi_path=str(self.dmi)),
                QD)
        sleep.assert_not_called()

    def test_links_are_relative_so_they_resolve_inside_pmon(self):
        self._select("QFX5241-64QD")
        for name, target in (("platform.json", "platform.json"),
                             ("media_settings.json", "media_settings.json"),
                             ("default_sku", "hwsku_name")):
            link = self._root(name)
            self.assertTrue(link.is_symlink(), name)
            self.assertEqual(str(link.readlink()), "%s/%s" % (QD, target))

    def test_reselecting_switches_between_skus(self):
        self._select("QFX5241-64QD")
        self.assertEqual(self._select("QFX5241-64OD"), OD)
        self.assertEqual(self._select("QFX5241-64QD"), QD)

    def test_missing_cache_leaves_shipped_files_alone(self):
        self.assertIsNone(mod.select_sku(
            cache=str(self.cache), dmi_path=str(self.dmi)))
        self.assertFalse(self._root("platform.json").is_symlink())

    def test_cache_without_product_name_leaves_files_alone(self):
        self.assertIsNone(self._select(None))
        self.assertFalse(self._root("default_sku").is_symlink())

    def test_without_wait_it_does_not_sleep(self):
        with mock.patch.object(mod.time, "sleep") as sleep:
            mod.select_sku(cache=str(self.cache), dmi_path=str(self.dmi))
        sleep.assert_not_called()

    def test_waits_for_the_cache_to_appear(self):
        def appear(_seconds):
            self.cache.write_bytes(tlv_cache("QFX5241-64QD"))
        with mock.patch.object(mod.time, "sleep", side_effect=appear) as sleep:
            self.assertEqual(mod.select_sku(
                wait=30, cache=str(self.cache),
                dmi_path=str(self.dmi)), QD)
        sleep.assert_called_once()

    def test_gives_up_after_the_wait(self):
        with mock.patch.object(mod.time, "sleep"), \
                mock.patch.object(mod.time, "monotonic",
                                  side_effect=[0, 1, 31]):
            self.assertIsNone(
                mod.select_sku(wait=30, cache=str(self.cache),
                               dmi_path=str(self.dmi)))

    def test_missing_sku_folder_is_reported_not_raised(self):
        for item in (self.platform_dir / QD).iterdir():
            item.unlink()
        (self.platform_dir / QD).rmdir()
        self.assertIsNone(self._select("QFX5241-64QD"))
        self.assertFalse(self._root("platform.json").is_symlink())

    def test_setup_survives_a_selection_failure(self):
        with mock.patch.object(mod, "select_sku",
                               side_effect=RuntimeError("boom")), \
                mock.patch.object(mod, "remove_stale_pmon_container") as pmon:
            mod.setup()
        pmon.assert_called_once()


class TestPostinst(unittest.TestCase):

    def test_postinst_selects_sku_only_on_a_running_system(self):
        text = (
            BASE_DIR / "debian" /
            "sonic-platform-juniper-qfx5241.postinst").read_text()
        guard = text.index("if [ -d /run/systemd/system ]")
        call = text.index("qfx5241_platform_init.py sku-symlinks || true")
        self.assertGreater(call, guard)

    def test_script_is_installed(self):
        manifest = (
            BASE_DIR / "debian" /
            "sonic-platform-juniper-qfx5241.install").read_text()
        self.assertIn(
            "qfx5241/utils/qfx5241_platform_init.py usr/local/bin", manifest)


if __name__ == "__main__":
    unittest.main()
