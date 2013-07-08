#
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

from vdsm.tool import expose

SEBOOL_ENABLED = "on"
SEBOOL_DISABLED = "off"

VDSM_SEBOOL_LIST = [
    "virt_use_fusefs",
    "virt_use_nfs",
    "virt_use_samba",
    "virt_use_sanlock",
    "sanlock_use_fusefs",
    "sanlock_use_nfs",
    "sanlock_use_samba",
]


def setup_booleans(status):
    # loading seobject is slow. Deferring its loading can reduce VDSM starting
    # time, because most utilities are and will be moved to vdsm-tool.
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


@expose("sebool-config")
def sebool_config():
    """Enable the required selinux booleans"""
    setup_booleans(True)


@expose("sebool-unconfig")
def sebool_unconfig():
    """Disable the required selinux booleans"""
    setup_booleans(False)
