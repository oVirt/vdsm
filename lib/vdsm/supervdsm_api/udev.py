# Copyright 2016-2017 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import
from __future__ import division

import os
import errno
import glob
import re
import logging

from vdsm.common import cmdutils
from vdsm.common import udevadm

from vdsm.constants import EXT_CHOWN, \
    DISKIMAGE_USER, DISKIMAGE_GROUP, \
    QEMU_PROCESS_USER, QEMU_PROCESS_GROUP

from . import expose

_UDEV_RULE_FILE_DIR = "/etc/udev/rules.d/"
_UDEV_RULE_FILE_PREFIX = "99-vdsm-"
_UDEV_RULE_FILE_EXT = ".rules"
_UDEV_RULE_FILE_NAME = os.path.join(
    _UDEV_RULE_FILE_DIR, _UDEV_RULE_FILE_PREFIX + '%s-%s' +
    _UDEV_RULE_FILE_EXT)
_UDEV_RULE_FILE_NAME_VFIO = os.path.join(
    _UDEV_RULE_FILE_DIR, _UDEV_RULE_FILE_PREFIX + "iommu_group_%s" +
    _UDEV_RULE_FILE_EXT)
_UDEV_RULE_FILE_NAME_HWRNG = os.path.join(
    _UDEV_RULE_FILE_DIR, _UDEV_RULE_FILE_PREFIX + "hwrng_%s" +
    _UDEV_RULE_FILE_EXT)
_UDEV_RULE_FILE_NAME_USB = os.path.join(
    _UDEV_RULE_FILE_DIR, _UDEV_RULE_FILE_PREFIX + "usb_%s_%s" +
    _UDEV_RULE_FILE_EXT)
_USB_DEVICE_PATH = '/dev/bus/usb/%03d/%03d'
_HWRNG_PATH = '/dev/hwrng'

_log = logging.getLogger("SuperVdsm.ServerCallback")


@expose
def udevTriggerMultipath(guid):
    _udevTrigger(property_matches=(('DM_NAME', guid),))


@expose
def appropriateMultipathDevice(guid, thiefId):
    ruleFile = _UDEV_RULE_FILE_NAME % (guid, thiefId)
    # WARNING: we cannot use USER, GROUP and MODE since using any of them
    # will change the selinux label to the default, causing vms to pause.
    # See https://bugzilla.redhat.com/1147910
    rule = 'SYMLINK=="mapper/%s", RUN+="%s %s:%s $env{DEVNAME}"\n' % (
        guid, EXT_CHOWN, DISKIMAGE_USER, DISKIMAGE_GROUP)
    with open(ruleFile, "w") as rf:
        _log.debug("Creating rule %s: %r", ruleFile, rule)
        rf.write(rule)


@expose
def rmAppropriateMultipathRules(thiefId):
    re_apprDevRule = "^" + _UDEV_RULE_FILE_PREFIX + ".*?-" + thiefId + \
        _UDEV_RULE_FILE_EXT + "$"
    rules = [os.path.join(_UDEV_RULE_FILE_DIR, r) for r in
             os.listdir(_UDEV_RULE_FILE_DIR)
             if re.match(re_apprDevRule, r)]
    fails = []
    for r in rules:
        try:
            _log.debug("Removing rule %s", r)
            os.remove(r)
        except OSError:
            fails.append(r)
    return fails


def _udevTrigger(*args, **kwargs):
    try:
        udevadm.trigger(*args, **kwargs)
    except cmdutils.Error as e:
        raise OSError(errno.EINVAL, 'Could not trigger change '
                      'out %s\nerr %s' % (e.out, e.err))


@expose
def appropriateHwrngDevice(vmId):
    ruleFile = _UDEV_RULE_FILE_NAME_HWRNG % (vmId,)
    rule = ('KERNEL=="hw_random" SUBSYSTEM=="misc" RUN+="%s %s:%s %s"\n' %
            (EXT_CHOWN, QEMU_PROCESS_USER, QEMU_PROCESS_GROUP, _HWRNG_PATH))
    with open(ruleFile, "w") as rf:
        _log.debug("Creating rule %s: %r", ruleFile, rule)
        rf.write(rule)

    _udevTrigger(subsystem_matches=('misc',))


@expose
def rmAppropriateHwrngDevice(vmId):
    rule_file = _UDEV_RULE_FILE_NAME_HWRNG % (vmId,)
    _log.debug("Removing rule %s", rule_file)
    try:
        os.remove(rule_file)
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise

    # Check that there are no other hwrng rules in place
    if not glob.glob(_UDEV_RULE_FILE_NAME_HWRNG % ('*',)):
        _log.debug('Changing ownership (to root:root) of device '
                   '%s', _HWRNG_PATH)
        os.chown(_HWRNG_PATH, 0, 0)
