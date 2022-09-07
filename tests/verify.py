# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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

            # NIC devices have an additional name and linkActive attributes
            if device['type'] == hwclass.NIC:
                self.assertIn('name', device)
                self.assertIn('linkActive', device)

        # Every alias has to be unique to the host. If this
        # condition doesn't hold, we may have identified the same XML chunk
        # as two different devices
        self.assertEqual(len(aliases), len(set(aliases)))
