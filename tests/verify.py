#
# Copyright 2015-2017 Red Hat, Inc.
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

from vdsm.virt.vmdevices import hwclass


class DeviceMixin(object):
    """
    Mixin that extends *TestCase class with method to verify device parsing
    from libvirt XML.
    """

    def verifyDevicesConf(self, conf):
        """
        Method to verify that the devices conf section of VM and optionally
        internal internal representation of VM devices is correct. This is very
        broad testing method only able to discover large issues.
        """
        aliases = []
        for device in conf:
            # IOMMU placeholder should be ignored
            if (device['type'] == hwclass.HOSTDEV and
                    'specParams' in device and
                    device['specParams'].get('iommuPlaceholder', False)):
                continue

            # Each device has alias.
            self.assertIn('alias', device)
            aliases.append(device['alias'])

            # Also, each device has an address with an exception of console,
            # balloon (which we treat as "none" balloon) and possibly a hostdev
            if device['type'] not in (hwclass.BALLOON, hwclass.HOSTDEV):
                self.assertIn('address', device)

            # NIC devices have an additional name and linkActive attributes
            if device['type'] == hwclass.NIC:
                self.assertIn('name', device)
                self.assertIn('linkActive', device)

        # Every alias has to be unique to the host. If this
        # condition doesn't hold, we may have identified the same XML chunk
        # as two different devices
        self.assertEqual(len(aliases), len(set(aliases)))
