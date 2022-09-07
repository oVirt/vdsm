# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import os
import pytest

from vdsm.common import cmdutils

from vdsm.storage import lvmconf


CONF = """
global {
    # For testing integer options
    use_lvmetad = 1
}

devices {
    # For testing list of strings
    filter = [ "a|^/dev/sda2$|", "r|.*|" ]

    # For testing commented value
    # gloabl_filter = [ "a|^/dev/sda2$|", "r|.*|" ]
}

activation {
    # For testing string options
    missing_stripe_filler = "error"
}
"""


def test_read_empty(tmpdir):
    path = create_lvm_conf(tmpdir, "")
    with lvmconf.LVMConfig(path) as conf:
        # No option - and no type information
        assert conf.getlist("devices", "filter") is None
        assert conf.getint("devices", "filter") is None
        assert conf.getstr("devices", "filter") is None
        # Commented is same as no option
        assert conf.getlist("devices", "global_filter") is None


def test_read_options(tmpdir):
    path = create_lvm_conf(tmpdir, CONF)
    with lvmconf.LVMConfig(path) as conf:
        # Correct type
        assert conf.getlist("devices", "filter") == ["a|^/dev/sda2$|", "r|.*|"]
        assert conf.getint("global", "use_lvmetad") == 1
        assert conf.getstr("activation", "missing_stripe_filler") == "error"

        # Wrong type
        assert conf.getstr("devices", "filter") is None
        assert conf.getint("devices", "filter") is None
        assert conf.getlist("global", "use_lvmetad") is None
        assert conf.getstr("global", "use_lvmetad") is None
        assert conf.getint("activation", "missing_stripe_filler") is None
        assert conf.getlist("activation", "missing_stripe_filler") is None


@pytest.mark.parametrize("data", [
    pytest.param(CONF, id="example"),
    pytest.param("", id="empty"),
])
def test_modify(tmpdir, data):
    path = create_lvm_conf(tmpdir, data)
    new_list = ["a|^/dev/sda2$|", "a|^/dev/sdb3$|", "r|.*|"]
    new_int = 0
    new_str = "ignore"

    with lvmconf.LVMConfig(path) as conf:
        old_list = conf.getlist("devices", "filter")
        old_int = conf.getint("global", "use_lvmetad")
        old_str = conf.getstr("activation", "missing_stripe_filler")

        conf.setlist("devices", "filter", new_list)
        conf.setint("global", "use_lvmetad", new_int)
        conf.setstr("activation", "missing_stripe_filler", new_str)

        assert conf.getlist("devices", "filter") == new_list
        assert conf.getint("global", "use_lvmetad") == new_int
        assert conf.getstr("activation", "missing_stripe_filler") == new_str

    with lvmconf.LVMConfig(path) as conf:
        assert conf.getlist("devices", "filter") == old_list
        assert conf.getint("global", "use_lvmetad") == old_int
        assert conf.getstr("activation", "missing_stripe_filler") == old_str


@pytest.mark.parametrize("data", [
    pytest.param(CONF, id="typcial"),
    pytest.param("", id="empty"),
])
def test_save(tmpdir, data):
    path = create_lvm_conf(tmpdir, data)
    new_list = ["a|^/dev/sda2$|", "a|^/dev/sdb3$|", "r|.*|"]
    new_int = 0
    new_str = "ignore"

    with lvmconf.LVMConfig(path) as conf:
        conf.setlist("devices", "filter", new_list)
        conf.setint("global", "use_lvmetad", new_int)
        conf.setstr("activation", "missing_stripe_filler", new_str)
        conf.save()

    with lvmconf.LVMConfig(path) as conf:
        assert conf.getlist("devices", "filter") == new_list
        assert conf.getint("global", "use_lvmetad") == new_int
        assert conf.getstr("activation", "missing_stripe_filler") == new_str


def test_save_backup(tmpdir):
    path = create_lvm_conf(tmpdir, CONF)

    with lvmconf.LVMConfig(path) as conf:
        old_filter = conf.getlist("devices", "filter")
        conf.setlist("devices", "filter", ["a|.*|"])
        conf.save()

    with lvmconf.LVMConfig(path + ".augsave") as backup:
        assert backup.getlist("devices", "filter") == old_filter


def test_save_error(tmpdir):
    path = create_lvm_conf(tmpdir, CONF)

    with lvmconf.LVMConfig(path) as conf:
        conf.setlist("devices", "filter", ["a|.*|"])
        # Make save fail even when runing as root.
        os.unlink(path)
        os.mkdir(path)
        with pytest.raises(EnvironmentError):
            conf.save()


def test_set_empty_list(tmpdir):
    path = create_lvm_conf(tmpdir, CONF)

    with lvmconf.LVMConfig(path) as conf:
        conf.setlist("devices", "filter", [])
        conf.save()

    with lvmconf.LVMConfig(path) as conf:
        assert conf.getlist("devices", "filter") is None


def test_remove(tmpdir):
    path = create_lvm_conf(tmpdir, CONF)

    with lvmconf.LVMConfig(path) as conf:
        conf.remove("devices", "filter")
        conf.save()

    with lvmconf.LVMConfig(path) as conf:
        assert conf.getlist("devices", "filter") is None


def test_save_keep_other_options(tmpdir):
    path = create_lvm_conf(tmpdir, CONF)

    with lvmconf.LVMConfig(path) as conf:
        conf.setlist("devices", "filter", ["a|.*|"])
        conf.save()

    with lvmconf.LVMConfig(path) as conf:
        assert conf.getint("global", "use_lvmetad") == 1


def test_context_exit_error(tmpdir):
    # Raise if __exit__ fails.
    path = create_lvm_conf(tmpdir, "")

    def fail():
        raise RuntimeError

    with pytest.raises(RuntimeError):
        with lvmconf.LVMConfig(path) as conf:
            conf.close = fail


def test_context_propagate_user_error(tmpdir):
    # Raise user errors if __exit__ fails.
    path = create_lvm_conf(tmpdir, "")

    def fail():
        raise RuntimeError

    class UserError(Exception):
        pass

    with pytest.raises(UserError):
        with lvmconf.LVMConfig(path) as conf:
            conf.close = fail
            raise UserError


@pytest.mark.skipif(not os.path.exists("/etc/lvm/lvm.conf"),
                    reason="lvm.conf not found")
def test_real_lvm_conf():
    with lvmconf.LVMConfig() as conf:
        assert conf.getint("global", "use_lvmetad") in (1, 0, None)


def test_configured_value_devices_dir():
    # Test lvmconf.configured_value() with value "devices/dir" which is very
    # unlikely to be something else than /dev.
    dev_dir = lvmconf.configured_value("devices", "dir")
    assert dev_dir == "/dev"


def test_non_existing_config_value():
    with pytest.raises(cmdutils.Error) as e:
        lvmconf.configured_value("not", "exists")
    assert e.value.rc == 5


def test_configured_value_empty_section():
    with pytest.raises(ValueError):
        lvmconf.configured_value("", "dir")


def test_configured_value_empty_option():
    with pytest.raises(ValueError):
        lvmconf.configured_value("devices", "")


def create_lvm_conf(tmpdir, data):
    path = tmpdir.join("lvm.conf")
    path.write(data)
    return str(path)
