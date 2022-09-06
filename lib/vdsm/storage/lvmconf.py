# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
lvmconf - Access LVM configuration file

This module provides the LVMConfig class, for reading and writing LVM
configuration. This class is a simple wrapper around augeas.Augeas, providing
easier to use, ConfigParser like interface for accessing options.


Reading configuration
---------------------

Use the get* methods::

    with lvmconf.LVMConfig(path) as conf:
        conf.getlist("devices", "filter")                   # ["a|.*|"]
        conf.getint("global", "use_lvmetad")                # 1
        conf.getstr("activation", "missing_stripe_filler")  # "error"

Unlike ConfigParser, reading integer options as strings does not work, so this
class does not provide untyped get() or set() method. Using the wrong type
returns None.


Modifying configuration
-----------------------

Use the set* methods::

    with lvmconf.LVMConfig(path) as conf:
        conf.setlist("devices", "section", ["a|^/dev/sda2$|", "r|.*|"])
        conf.setint("global", "use_lvmetad", 0)
        conf.setstr("activation", "missing_stripe_filler", "ignore")
        conf.save()

We use augeas backup option to save the previous file at file.augsave.

Anyone can read LVM configuration, but to modify it you must run as root.


TODO
----

- Add "# Vdsm: description..." comment when modifying a value possible with
  tricky XPATH voodoo.

- Add blank line before new options seems that is not supported by augeas.

- Insert options after at the default location, under the default comment.
  Should be possible using tricky XPATH and lot of work.

- Indent new options properly - seems that it is not supported in augeas.

"""

import logging

from augeas import Augeas

from vdsm import constants
from vdsm.common import commands


log = logging.getLogger("storage.lvmconf")


class UnexpectedLvmConfigOutput(Exception):
    msg = "Unexpected LVM config output"

    def __init__(self, reason):
        self.value = "reason=%s" % reason

    def __str__(self):
        return "%s: %s" % (self.msg, repr(self.value))


class LVMConfig(object):

    def __init__(self, path="/etc/lvm/lvm.conf"):
        self.path = path

        # Augeas loads by default tons of unneeded lenses and configuration
        # files. On my test host, it fails to load, trying to read my 500 MiB
        # /etc/lvm/archive/.
        #
        # These are the standard LVM lens includes:
        # /augeas/load/LVM/incl[1] /etc/lvm/lvm.conf
        # /augeas/load/LVM/incl[2] /etc/lvm/backup/*
        # /augeas/load/LVM/incl[3] /etc/lvm/archive/*.vg
        #
        # We need only the first entry to work with lvm.conf. Using customized
        # load setup, as explained in
        # https://github.com/hercules-team/augeas/wiki/Loading-specific-files
        #
        # Removing the archive and backup entries, we can load augeas in 0.7
        # seconds on my test vm. Removing all other lenses shorten the time to
        # 0.04 seconds.

        log.debug("Loading LVM configuration from %r", path)
        self.aug = Augeas(flags=Augeas.NO_MODL_AUTOLOAD | Augeas.SAVE_BACKUP)
        self.aug.add_transform("lvm.lns", [path])
        self.aug.load()

    # Context manager interface

    def __enter__(self):
        return self

    def __exit__(self, t, v, tb):
        try:
            self.close()
        except Exception as e:
            # Caller succeeded, raise the close error.
            if t is None:
                raise
            # Caller has failed, do not hide the original error.
            log.exception("Error closing %s: %s" % (self, e))

    # Accessing list of strings

    def getlist(self, section, option):
        pat = "/files%s/%s/dict/%s/list/*/str" % (self.path, section, option)
        matches = self.aug.match(pat)
        if not matches:
            return None  # Cannot store/read empty list
        return [self.aug.get(m) for m in matches]

    def setlist(self, section, option, value):
        log.debug("Setting %s/%s to %s", section, option, value)
        opt_path = "/files%s/%s/dict/%s" % (self.path, section, option)
        self.aug.remove(opt_path)
        item_path = opt_path + "/list/%d/str"
        for i, item in enumerate(value, 1):
            self.aug.set(item_path % i, item)

    # Accessing flat values (int, string)

    def getint(self, section, option):
        val = self._get_flat(section, option, "int")
        return int(val) if val is not None else None

    def setint(self, section, option, value):
        self._set_flat(section, option, "int", str(value))

    def getstr(self, section, option):
        return self._get_flat(section, option, "str")

    def setstr(self, section, option, value):
        self._set_flat(section, option, "str", value)

    def _get_flat(self, section, option, opt_type):
        path = self._flat_path(section, option, opt_type)
        return self.aug.get(path)

    def _set_flat(self, section, option, opt_type, value):
        log.debug("Setting %s/%s to %r", section, option, value)
        path = self._flat_path(section, option, opt_type)
        return self.aug.set(path, value)

    def _flat_path(self, section, option, opt_type):
        return "/files%s/%s/dict/%s/%s" % (
            self.path, section, option, opt_type)

    # Removing options

    def remove(self, section, option):
        log.debug("Removing %s/%s", section, option)
        path = "/files%s/%s/dict/%s" % (self.path, section, option)
        self.aug.remove(path)

    # File operations

    def save(self):
        log.info("Saving new LVM configuration to %r, previous configuration "
                 "saved to %r",
                 self.path, self.path + ".augsave")
        self.aug.save()

    def close(self):
        log.debug("Closing LVM configuration %s", self.path)
        self.aug.close()


def configured_value(section, option):
    """
    Return value configured for the option, taken into account all config
    files and default configurations.
    """
    if not section:
        raise ValueError("Section must not be empty.")
    if not option:
        raise ValueError("Option must not be empty.")

    cmd = [constants.EXT_LVM, "lvmconfig",
           "--typeconfig", "full",
           f"{section}/{option}"]
    out = commands.run(cmd).decode("utf-8").strip()

    # Validate, that the output is in expected format key=value and doesn't
    # span multiple lines.
    if "=" not in out or "\n" in out:
        raise UnexpectedLvmConfigOutput(
            f"Unexpected output: option={section}/{option}, output={out}")

    key, value = out.split("=", 1)

    # Validate, that returned key matches requested option.
    if key.strip() != option:
        raise UnexpectedLvmConfigOutput(
            f"Returned key doesn't match option: option={option}, key={key}")

    # String value.
    if value[0] == '"' and value[-1] == '"':
        value = value[1:-1]

    return value
