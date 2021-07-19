# Copyright 2012 Red Hat, Inc.
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

import sys

from . import YES, NO, MAYBE
from vdsm import utils

SEBOOL_ENABLED = "on"
SEBOOL_DISABLED = "off"

VDSM_SEBOOL_LIST = (
    "virt_use_fusefs",
    "virt_use_nfs",
    "virt_use_samba",
    "virt_use_sanlock",
    "sanlock_use_fusefs",
    "sanlock_use_nfs",
    "sanlock_use_samba",
)


def _setup_booleans(status):
    # loading seobject is slow. Deferring its loading can reduce VDSM
    # startings time, because most utilities are and will be moved
    # to vdsm-tool.
    import seobject

    sebool_obj = seobject.booleanRecords()
    sebool_status = sebool_obj.get_all()

    sebool_obj.start()

    for sebool_variable in VDSM_SEBOOL_LIST:
        if status and not all(sebool_status[sebool_variable]):
            sebool_obj.modify(sebool_variable, SEBOOL_ENABLED)

        if not status and any(sebool_status[sebool_variable]):
            sebool_obj.modify(sebool_variable, SEBOOL_DISABLED)

    sebool_obj.finish()


def isconfigured():
    """
    True all selinux booleans in the list above are set properly
    """
    ret = YES
    if utils.get_selinux_enforce_mode() == -1:
        ret = MAYBE
        _log("WARNING: SELinux is disabled!")
    else:
        import seobject
        sebool_obj = seobject.booleanRecords()
        sebool_status = sebool_obj.get_all()

        for sebool_variable in VDSM_SEBOOL_LIST:
            if not all(sebool_status[sebool_variable]):
                ret = NO

    return ret


def configure():
    """
    Configure selinux booleans (see list above)
    """
    if utils.get_selinux_enforce_mode() > -1:
        _setup_booleans(True)
    else:
        _log(
            "WARNING: SELinux is disabled! "
            "Skipping SELinux boolean configuration."
        )


def removeConf():
    """
    Disabling selinux booleans (see list above)
    """
    if utils.get_selinux_enforce_mode() > -1:
        _setup_booleans(False)
    else:
        _log(
            "WARNING: SELinux is disabled! "
            "Skipping removal of SELinux booleans."
        )


# TODO: use standard logging
def _log(fmt, *args):
    sys.stdout.write(fmt % args + "\n")
