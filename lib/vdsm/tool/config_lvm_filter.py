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
from __future__ import division
from __future__ import print_function

import argparse

from vdsm.storage import lvmconf
from vdsm.storage import lvmfilter

from . import expose
from . import common

_NAME = 'config-lvm-filter'


@expose(_NAME)
def main(*args):
    """
    config-lvm-filter
    Configure LVM filter allowing LVM to access only the local storage
    needed by the hypervisor, but not shared storage owned by Vdsm.
    """
    args = parse_args(args)

    print("Analyzing host...")

    mounts = lvmfilter.find_lvm_mounts()
    wanted_filter = lvmfilter.build_filter(mounts)

    with lvmconf.LVMConfig() as config:
        current_filter = config.getlist("devices", "filter")

    advice = lvmfilter.analyze(current_filter, wanted_filter)

    # This is the expected condition on a correctly configured host.
    if advice.action == lvmfilter.UNNEEDED:
        print("LVM filter is already configured for Vdsm")
        return

    # We need to configure LVM filter.

    print("Found these mounted logical volumes on this host:")
    print()

    for mnt in mounts:
        print("  logical volume: ", mnt.lv)
        print("  mountpoint:     ", mnt.mountpoint)
        print("  devices:        ", ", ".join(mnt.devices))
        print()

    print("This is the recommended LVM filter for this host:")
    print()
    print("  " + lvmfilter.format_option(wanted_filter))
    print()
    print("""\
This filter allows LVM to access the local devices used by the
hypervisor, but not shared storage owned by Vdsm. If you add a new
device to the volume group, you will need to edit the filter manually.
""")

    if current_filter:
        print("This is the current LVM filter:")
        print()
        print("  " + lvmfilter.format_option(current_filter))
        print()

    if advice.action == lvmfilter.CONFIGURE:

        if not args.assume_yes:
            if not common.confirm("Configure LVM filter? [yes,NO] "):
                return

        with lvmconf.LVMConfig() as config:
            config.setlist("devices", "filter", advice.filter)
            config.save()

        print("""\
Configuration completed successfully!

Please reboot to verify the LVM configuration.
""")

    elif advice.action == lvmfilter.RECOMMEND:

        print("""\
WARNING: The current LVM filter does not match the recommended filter,
Vdsm cannot configure the filter automatically.

Please edit /etc/lvm/lvm.conf and set the 'filter' option in the
'devices' section to the recommended value.

It is recommend to reboot after changing LVM filter.
""")


def parse_args(args):
    parser = argparse.ArgumentParser(prog="vdsm-tool config-lvm-filter")

    parser.add_argument(
        "-y", "--assume-yes",
        action="store_true",
        help="Automatically answer yes for all questions")

    return parser.parse_args(args[1:])
