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

from vdsm import utils
from hostdev import get_device_params, detach_detachable, \
    SkipIOMMUPLaceholderDevice, CAPABILITY_TO_XML_ATTR
from . import core
from . import hwclass


class HostDevice(core.Base):
    __slots__ = ('address', 'hostAddress', 'bootOrder', '_deviceParams',
                 'macAddr', 'vlanId')

    def __init__(self, conf, log, **kwargs):
        super(HostDevice, self).__init__(conf, log, **kwargs)

        self._deviceParams = get_device_params(self.device)

        self.macAddr = self.specParams.get('macAddr')
        self.vlanId = self.specParams.get('vlanId')
        self.hostAddress = self._deviceParams.get('address')

    def detach(self):
        """
        Detach the device from the host. This method *must* be
        called before getXML in order to populate _deviceParams.
        """
        self._deviceParams = detach_detachable(self.device)

    def getXML(self):
        if any((self.macAddr, self.vlanId)):
            xml = self._create_network_interface_xml()
        else:
            xml = self._create_generic_hostdev_xml()

        if hasattr(self, 'bootOrder'):
            xml.appendChildWithArgs('boot', order=self.bootOrder)

        if hasattr(self, 'address'):
            self._add_source_address(xml)

        return xml

    def _create_generic_hostdev_xml(self):
        """
            Create domxml for a host device.

            <devices>
                <hostdev mode='subsystem' type='pci' managed='no'>
                <source>
                    <address domain='0x0000' bus='0x06' slot='0x02'
                    function='0x0'/>
                </source>
                <boot order='1'/>
                </hostdev>
            </devices>
            """

        if (CAPABILITY_TO_XML_ATTR[
                self._deviceParams['capability']] == 'pci' and
                utils.tobool(self.specParams.get('iommuPlaceholder', False))):
            raise SkipIOMMUPLaceholderDevice

        hostdev = self.createXmlElem(hwclass.HOSTDEV, None)
        hostdev.setAttrs(
            managed='no', mode='subsystem',
            type=CAPABILITY_TO_XML_ATTR[self._deviceParams['capability']])
        source = hostdev.appendChildWithArgs('source')

        self._add_source_address(source)

        return hostdev

    def _create_network_interface_xml(self):
        """
        Create domxml for a host device.

        <devices>
         <interface type='hostdev' managed='no'>
          <driver name='vfio'/>
          <source>
           <address type='pci' domain='0x0000' bus='0x00' slot='0x07'
           function='0x0'/>
          </source>
          <mac address='52:54:00:6d:90:02'/>
          <vlan>
           <tag id=100/>
          </vlan>
          <boot order='1'/>
         </interface>
        </devices>
        """
        interface = self.createXmlElem(hwclass.NIC, hwclass.HOSTDEV)
        interface.setAttrs(managed='no')
        interface.appendChildWithArgs('driver', name='vfio')
        source = interface.appendChildWithArgs('source')
        self._add_source_address(source, type='pci')

        if self.macAddr is not None:
            interface.appendChildWithArgs('mac', address=self.macAddr)
        if self.vlanId is not None:
            vlan = interface.appendChildWithArgs('vlan')
            vlan.appendChildWithArgs('tag', id=str(self.vlanId))

        return interface

    def _add_source_address(self, parent_element, type=None):
        parent_element.appendChildWithArgs('address', type=type,
                                           **self._deviceParams['address'])
