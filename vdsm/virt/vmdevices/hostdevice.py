#
# Copyright 2015 Red Hat, Inc.
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

from hostdev import get_device_params, detach_detachable
from . import core
from . import hwclass


class HostDevice(core.Base):
    __slots__ = ('address', 'bootOrder', '_deviceParams')

    def __init__(self, conf, log, **kwargs):
        super(HostDevice, self).__init__(conf, log, **kwargs)

        self._deviceParams = get_device_params(self.device)

    def detach(self):
        """
        Detach the device from the host. This method *must* be
        called before getXML in order to populate _deviceParams.
        """
        self._deviceParams = detach_detachable(self.device)

    def getXML(self):
        """
        Create domxml for a host device.

        <devices>
            <hostdev mode='subsystem' type='pci' managed='no'>
            <source>
                <address domain='0x0000' bus='0x06' slot='0x02'
                function='0x0'/>
            </source>
            <boot order='1'/>
            <rom bar='on' file='/etc/fake/boot.bin'/>
            </hostdev>
        </devices>
        """
        hostdev = self.createXmlElem(hwclass.HOSTDEV, None)
        hostdev.setAttrs(managed='no', mode='subsystem',
                         type=self._deviceParams['capability'])
        source = hostdev.appendChildWithArgs('source')

        if hasattr(self, 'bootOrder'):
            hostdev.appendChildWithArgs(
                'boot', order=self.bootOrder)

        if self._deviceParams['capability'] == 'pci':
            source.appendChildWithArgs('address', None,
                                       **self._deviceParams['address'])
            rom = hostdev.appendChildWithArgs('rom')
            romAttrs = {}

            if 'bar' in self.specParams:
                if self.specParams['bar']:
                    romAttrs['bar'] = 'on'
                else:
                    romAttrs['bar'] = 'off'
                if 'file' in self.specParams:
                    romAttrs['file'] = self.specParams['file']

            rom.setAttrs(**romAttrs)

        if hasattr(self, 'address'):
            hostdev.appendChildWithArgs('address', None, **self.address)

        return hostdev
