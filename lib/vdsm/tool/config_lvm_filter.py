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
import textwrap

from vdsm.common.config import config
from vdsm.storage import lvmconf
from vdsm.storage import lvmdevices
from vdsm.storage import lvmfilter
from vdsm.storage import mpathconf

from . import expose
from . import common

_NAME = 'config-lvm-filter'

# Return codes:
# rc=0 will be exited by vdsm-tool in case flow ended successfully.
# rc=1 will be exited by vdsm-tool in case an exception was raised.
# rc=2 will be exited from parse_args() in case of invalid usage.
CANNOT_CONFIG = 3
NEEDS_CONFIG = 4


@expose(_NAME)
def main(*args):
    """
    config-lvm-filter
    Configure LVM filter allowing LVM to access only the local storage
    needed by the hypervisor, but not shared storage owned by Vdsm.

    Return codes:
        0 - Successful completion.
        1 - Exception caught during operation.
        2 - Wrong arguments.
        3 - LVM filter configuration was found to be required but could not be
            completed since there is already another filter configured on the
            host.
        4 - User has chosen not to allow LVM filter reconfiguration, although
            found as required.
    """
    args = parse_args(args)

    print("Analyzing host...")

    config_method = config.get("lvm", "config_method").lower()

    if config_method == "filter":
        return config_with_filter(args)
    elif config_method == "devices":
        return config_with_devices(args)
    else:
        print("Unknown configuration method %s, use either 'filter' or "
              "'devices'." % config_method)
        return CANNOT_CONFIG


def config_with_filter(args):
    mounts = lvmfilter.find_lvm_mounts()
    wanted_wwids = lvmfilter.find_wwids(mounts)
    current_wwids = mpathconf.read_blacklist()
    wanted_filter = lvmfilter.build_filter(mounts)
    with lvmconf.LVMConfig() as lvm_config:
        current_filter = lvm_config.getlist("devices", "filter")

    advice = lvmfilter.analyze(
        current_filter,
        wanted_filter,
        current_wwids,
        wanted_wwids)

    # This is the expected condition on a correctly configured host.
    if advice.action == lvmfilter.UNNEEDED:
        print("LVM filter is already configured for Vdsm")
        return

    # We need to configure LVM filter.

    _print_summary(mounts, current_filter, wanted_filter, advice.wwids, None)

    if advice.action == lvmfilter.CONFIGURE:

        if not args.assume_yes:
            if not common.confirm("Configure host? [yes,NO] "):
                return NEEDS_CONFIG

        mpathconf.configure_blacklist(advice.wwids)

        with lvmconf.LVMConfig() as config:
            config.setlist("devices", "filter", advice.filter)
            config.setint("devices", "use_devicesfile", 0)
            config.save()

        _print_success()

    elif advice.action == lvmfilter.RECOMMEND:
        _print_filter_warning()
        return CANNOT_CONFIG


def config_with_devices(args):

    # Check if the lvm devices is already configured.
    if lvmdevices.is_configured():
        print("LVM devices already configured for vdsm")
        return

    mounts = lvmfilter.find_lvm_mounts()
    wanted_wwids = lvmfilter.find_wwids(mounts)
    current_wwids = mpathconf.read_blacklist()

    # Find VGs of mounted devices.
    vgs = {mnt.vg_name for mnt in mounts}

    # Print config summary for the user.
    _print_summary(mounts, None, None, wanted_wwids, vgs)

    if not args.assume_yes:
        if not common.confirm("Configure host? [yes,NO] "):
            return NEEDS_CONFIG

    # Before creating devices file we have to also configure multipath
    # blacklist.
    if current_wwids != wanted_wwids:
        mpathconf.configure_blacklist(wanted_wwids)

    # Enable lvm devices, configure devices file and remove lvm filter.
    lvmdevices.configure(vgs)

    _print_success()


def parse_args(args):
    parser = argparse.ArgumentParser(prog="vdsm-tool config-lvm-filter")

    parser.add_argument(
        "-y", "--assume-yes",
        action="store_true",
        help="Automatically answer yes for all questions")

    return parser.parse_args(args[1:])


def _print_mounts(mounts):
    print("Found these mounted logical volumes on this host:")
    print()

    for mnt in mounts:
        print("  logical volume: ", mnt.lv)
        print("  mountpoint:     ", mnt.mountpoint)
        print("  devices:        ", ", ".join(mnt.devices))
        print()


def _print_recommended_filter(wanted_filter):
    print("This is the recommended LVM filter for this host:")
    print()
    print("  " + lvmfilter.format_option(wanted_filter))
    print()
    print("""\
This filter allows LVM to access the local devices used by the
hypervisor, but not shared storage owned by Vdsm. If you add a new
device to the volume group, you will need to edit the filter manually.
    """)


def _print_current_filter(current_filter):
    print("This is the current LVM filter:")
    print()
    print("  " + lvmfilter.format_option(current_filter))
    print()


def _print_wanted_blacklist(wanted_wwids):
    print("To properly configure the host, we need to add multipath")
    print("blacklist in /etc/multipath/conf.d/vdsm_blacklist.conf:")
    print()
    print(textwrap.indent(mpathconf.format_blacklist(wanted_wwids), "  "))
    print()


def _print_success():
    print("""\
Configuration completed successfully!

Please reboot to verify the configuration.
    """)


def _print_filter_warning():
    print("""\
WARNING: The current LVM filter does not match the recommended filter,
Vdsm cannot configure the filter automatically.

Please edit /etc/lvm/lvm.conf and set the 'filter' option in the
'devices' section to the recommended value. Also make sure that
'use_devicesfile' in this section is set to 0.

Make sure /etc/multipath/conf.d/vdsm_blacklist.conf is set with the
recommended 'blacklist' section.

It is recommended to reboot to verify the new configuration.
    """)


def _print_devices_info(vgs):
    print("""\
Configuring LVM system.devices.
Devices for following VGs will be imported:
    """)
    print(f" {', '.join(vgs)}")
    print()


def _print_summary(mounts, current_filter, wanted_filter, advice_wwids, vgs):
    _print_mounts(mounts)

    if wanted_filter:
        _print_recommended_filter(wanted_filter)

    if vgs:
        _print_devices_info(vgs)

    if current_filter:
        _print_current_filter(current_filter)

    if advice_wwids:
        _print_wanted_blacklist(advice_wwids)
