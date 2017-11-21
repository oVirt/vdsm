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

import os
import errno
import glob
import re
import logging

from vdsm import udevadm
from vdsm.common import cmdutils
from vdsm.common import commands
from vdsm.common import fileutils

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
def appropriateSCSIDevice(device_name, udev_path):
    ruleFile = _UDEV_RULE_FILE_NAME % ('scsi', device_name)
    rule = 'KERNEL=="%s" SUBSYSTEM=="scsi_generic" RUN+="%s %s:%s %s"\n' % (
        os.path.basename(udev_path), EXT_CHOWN, QEMU_PROCESS_USER,
        QEMU_PROCESS_GROUP, udev_path)
    with open(ruleFile, "w") as rf:
        _log.debug("Creating rule %s: %r", ruleFile, rule)
        rf.write(rule)

    _udevTrigger(subsystem_matches=('scsi_generic',))


@expose
def rmAppropriateSCSIDevice(device_name, udev_path):
    rule_file = _UDEV_RULE_FILE_NAME % ('scsi', device_name)
    _log.debug("Removing rule %s", rule_file)
    fileutils.rm_file(rule_file)

    _log.debug('Changing ownership (to root:disk) of device %s', udev_path)
    cmd = [EXT_CHOWN, 'root:disk', udev_path]
    rc, out, err = commands.execCmd(cmd)
    if err:
        raise OSError(errno.EINVAL, 'Could not change ownership'
                      'out %s\nerr %s' % (out, err))


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


@expose
def appropriateIommuGroup(iommu_group):
    """
    Create udev rule in /etc/udev/rules.d/ to change ownership
    of /dev/vfio/$iommu_group to qemu:qemu. This method should be called
    when detaching a device from the host.
    """
    rule_file = _UDEV_RULE_FILE_NAME_VFIO % iommu_group

    if not os.path.isfile(rule_file):
        # If the file exists, different device from the same group has
        # already been detached and we therefore can skip overwriting the
        # file. Also, this file should only be created/removed via the
        # means of supervdsm.

        rule = ('KERNEL=="{}", SUBSYSTEM=="vfio" RUN+="{} {}:{} '
                '/dev/vfio/{}"').format(iommu_group, EXT_CHOWN,
                                        QEMU_PROCESS_USER,
                                        QEMU_PROCESS_GROUP,
                                        iommu_group)

        with open(rule_file, "w") as rf:
            _log.debug("Creating rule %s: %r", rule_file, rule)
            rf.write(rule)

        _udevTrigger(subsystem_matches=('vfio',))


@expose
def rmAppropriateIommuGroup(iommu_group):
    """
    Remove udev rule in /etc/udev/rules.d/ created by
    vfioAppropriateDevice.
    """
    rule_file = os.path.join(_UDEV_RULE_FILE_DIR, _UDEV_RULE_FILE_PREFIX +
                             "iommu_group_" + iommu_group +
                             _UDEV_RULE_FILE_EXT)
    error = False

    try:
        os.remove(rule_file)
    except OSError as e:
        if e.errno == errno.ENOENT:
            # OSError with ENOENT errno here means that the rule file does
            # not exist - this is expected when multiple devices in one
            # iommu group were passed through.
            error = True
        else:
            raise
    else:
        _log.debug("Removing rule %s", rule_file)

    if not error:
        _udevTrigger(subsystem_matches=('vfio',))


@expose
def appropriateUSBDevice(bus, device):
    rule_file = _UDEV_RULE_FILE_NAME_USB % (bus, device)
    rule = ('SUBSYSTEM=="usb", ATTRS{{busnum}}=="{}", '
            'ATTRS{{devnum}}=="{}", OWNER:="{}", GROUP:="{}"\n').format(
        bus, device, QEMU_PROCESS_USER, QEMU_PROCESS_GROUP)

    _log.debug("Creating rule %s: %r", rule_file, rule)

    with open(rule_file, "w") as rf:
        rf.write(rule)

    _udevTrigger(attr_matches=(('busnum', int(bus)),
                               ('devnum', int(device))))


@expose
def rmAppropriateUSBDevice(bus, device):
    rule_file = _UDEV_RULE_FILE_NAME_USB % (bus, device)
    _log.debug("Removing rule %s", rule_file)
    try:
        os.remove(rule_file)
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise

        _log.warning('Rule %s missing', rule_file)

    _log.debug('Changing ownership (to root:root) of device '
               'bus: %s, device:: %s', bus, device)
    device_path = _USB_DEVICE_PATH % (int(bus), int(device))
    cmd = [EXT_CHOWN, 'root:root', device_path]
    rc, out, err = commands.execCmd(cmd)
    if err:
        raise OSError(errno.EINVAL, 'Could not change ownership'
                      'out %s\nerr %s' % (out, err))

    # It's possible that the device was in input class or had rule
    # matched against it, trigger to make sure everything is fine.
    _udevTrigger(attr_matches=(('busnum', int(bus)),
                               ('devnum', int(device))))


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
