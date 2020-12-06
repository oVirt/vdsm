# Copyright 2017-2019 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import division

import errno
import os
import shutil
import sys
import time

from vdsm.common import commands
from vdsm.common import systemctl
from vdsm.common.cmdutils import CommandPath
from vdsm.storage import fileUtils
from vdsm.tool import confmeta

from . import YES, NO

_SYSTEMCTL = CommandPath("systemctl", "/bin/systemctl", "/usr/bin/systemctl")

# TODO: use constants.py
_LVMLOCAL_CUR = "/etc/lvm/lvmlocal.conf"
_LVMLOCAL_VDSM = "/usr/share/vdsm/lvmlocal.conf"
_LVMETAD_SERVICE = "lvm2-lvmetad.service"
_LVMETAD_SOCKET = "lvm2-lvmetad.socket"


# Configuratior interface
name = "lvm"
services = []


def configure():
    """
    Disable and mask lvmetad daemon, and install vdsm managed lvmlocal.conf.
    """
    if not _lvm_conf_configured():
        _backup_file(_LVMLOCAL_CUR)

        # TODO: we should merge the contents of the exisiting file and vdsm
        # settings, in case the user has some useful setting in the
        # lvmlocal.conf.
        _log("Installing %s at %s", _LVMLOCAL_VDSM, _LVMLOCAL_CUR)
        with open(_LVMLOCAL_VDSM, "rb") as f:
            fileUtils.atomic_write(_LVMLOCAL_CUR, f.read(), relabel=True)

    # TODO: remove disabling lvmetad once we don't support Fedora 30. On
    # Fedora 31 and RHEL8 lvmetad is not supported anymore.
    if not _lvmetad_configured():
        _systemctl("mask", _LVMETAD_SERVICE, _LVMETAD_SOCKET)
        _systemctl("disable", _LVMETAD_SERVICE, _LVMETAD_SOCKET)
        _systemctl("stop", _LVMETAD_SERVICE, _LVMETAD_SOCKET)


def isconfigured():
    """
    Return YES if lvmetad service and socket are disabled and masked, and
    /etc/lvm/lvmlocal.conf is using the correct version, or is marked as
    private. Otherwise return NO.
    """
    # TODO: we don't need to check if lvmetad is disabled once we don't support
    # Fedora 30. On Fedora 31 and RHEL8 lvmetad is not supported anymore.
    if _lvm_conf_configured() and _lvmetad_configured():
        _log("lvm is configured for vdsm")
        return YES
    else:
        _log("lvm requires configuration")
        return NO


def _lvm_conf_configured():
    """
    Return True if lvm local conf is using the correct version or is private,
    otherwise return False.
    """
    try:
        cur_conf = confmeta.read_metadata(_LVMLOCAL_CUR)
    except EnvironmentError as e:
        if e.errno != errno.ENOENT:
            raise
        return False

    if cur_conf.revision is None:
        # LVM installs a default lvmlocal.conf with documention for the "local"
        # section. We backup this file and replace it with vdsm version.
        _log("WARNING: LVM local configuration: %s is not based on vdsm "
             "configuration", _LVMLOCAL_CUR)
        return False

    vdsm_conf = confmeta.read_metadata(_LVMLOCAL_VDSM)
    if cur_conf.private:
        # Using private configuration is ok
        _log("Using private lvm local configuration: %s", _LVMLOCAL_CUR)
        if cur_conf.revision < vdsm_conf.revision:
            # But using outated configuration is not. The admin should update
            # the file revision to avoid this warning.
            _log("WARNING: Installed lvm local configuration: %s was based "
                 "on an older revision. Please update the file form vdsm "
                 "configuration: %s", _LVMLOCAL_CUR, _LVMLOCAL_VDSM)
        return True

    return vdsm_conf.revision == cur_conf.revision


def _lvmetad_configured():
    """
    Return True if both lvmetad service and socket are masked and disabled,
    otherwise return False.

    TODO: remove this function once we don't support Fedora 30. On Fedora 31
    and RHEL8 lvmetad is not supported anymore.
    """
    pattern = "lvm2-lvmetad*"
    properties = ("Names", "LoadState", "ActiveState")
    units = systemctl.show(pattern, properties=properties)

    if not units:
        # There's no lvmetad and thus nothing to configure
        return True

    not_configured = []
    for unit in units:
        # ActiveState may be "inactive" or "failed", both are good.
        if unit["LoadState"] != "masked" or unit["ActiveState"] == "active":
            not_configured.append(unit)

    if not_configured:
        _log("Units need configuration: %s", not_configured)
        return False

    return True


def _backup_file(path):
    """
    Backup current file with a timestamp.

    TODO: Same code is used in multipath configurator, so this should move to
    tool utils module. Keeping here for now to make it easier to backport.
    """
    if os.path.exists(path):
        backup = path + '.' + time.strftime("%Y%m%d%H%M")
        _log("Backing up %s to %s", path, backup)
        shutil.copyfile(path, backup)


def _systemctl(*args):
    cmd = [_SYSTEMCTL.cmd]
    cmd.extend(args)
    return commands.run(cmd)


# TODO: use standad logging
def _log(fmt, *args):
    sys.stdout.write(fmt % args + "\n")
