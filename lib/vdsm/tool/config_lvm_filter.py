# Copyright 2017 Red Hat, Inc.
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
from __future__ import print_function

from . import expose

from vdsm.storage import lvmfilter

_NAME = 'config-lvm-filter'


@expose(_NAME)
def main(*args):
    """
    config-lvm-filter
    Configure LVM filter allowing LVM to access only the local storage needed
    by the hypervisor, but not shared storage owned by Vdsm.
    """
    mounts = lvmfilter.find_lvm_mounts()

    print("Found these mounted logical volumes on this host:")
    print()

    for mnt in mounts:
        print("  logical volume: ", mnt.lv)
        print("  mountpoint:     ", mnt.mountpoint)
        print("  devices:        ", ", ".join(mnt.devices))
        print()

    lvm_filter = lvmfilter.build_filter(mounts)

    print("This is the recommended LVM filter for this host:")
    print()
    print("  " + lvmfilter.format_option(lvm_filter))
    print()

    print("To use this LVM filter please edit /etc/lvm/lvm.conf\n"
          "and set the 'filter' option in the 'devices' section.\n"
          "It is recommended to reboot the hypervisor to verify the\n"
          "filter.\n"
          "\n"
          "This filter will allow LVM to access the local devices used\n"
          "by the hypervisor, but not shared storage owned by Vdsm.\n"
          "If you want to add another local device you will have to\n"
          "add the device manually to the LVM filter.\n")
