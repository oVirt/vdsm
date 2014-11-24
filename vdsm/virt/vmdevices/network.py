#
# Copyright 2008-2014 Red Hat, Inc.
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

from .core import Base


class Interface(Base):
    __slots__ = ('nicModel', 'macAddr', 'network', 'bootOrder', 'address',
                 'linkActive', 'portMirroring', 'filter',
                 'sndbufParam', 'driver', 'name')

    def __init__(self, conf, log, **kwargs):
        # pyLint can't tell that the Device.__init__() will
        # set a nicModel attribute, so modify the kwarg list
        # prior to device init.
        for attr, value in kwargs.iteritems():
            if attr == 'nicModel' and value == 'pv':
                kwargs[attr] = 'virtio'
            elif attr == 'network' and value == '':
                kwargs[attr] = DUMMY_BRIDGE
        super(Interface, self).__init__(conf, log, **kwargs)
        self.sndbufParam = False
        self._customize()

    def _customize(self):
        # Customize network device
        self.driver = {}

        vhosts = self._getVHostSettings()
        if vhosts:
            self.driver['name'] = vhosts.get(self.network, False)

        try:
            self.driver['queues'] = self.custom['queues']
        except KeyError:
            pass    # interface queues not specified

        try:
            self.sndbufParam = self.conf['custom']['sndbuf']
        except KeyError:
            pass    # custom_sndbuf not specified

    def _getVHostSettings(self):
        VHOST_MAP = {'true': 'vhost', 'false': 'qemu'}
        vhosts = {}
        vhostProp = self.conf.get('custom', {}).get('vhost', '')

        if vhostProp != '':
            for vhost in vhostProp.split(','):
                try:
                    vbridge, vstatus = vhost.split(':', 1)
                    vhosts[vbridge] = VHOST_MAP[vstatus.lower()]
                except (ValueError, KeyError):
                    self.log.warning("Unknown vhost format: %s", vhost)

        return vhosts

    def getXML(self):
        """
        Create domxml for network interface.

        <interface type="bridge">
            <mac address="aa:bb:dd:dd:aa:bb"/>
            <model type="virtio"/>
            <source bridge="engine"/>
            [<driver name="vhost/qemu" queues="int"/>]
            [<filterref filter='filter name'/>]
            [<tune><sndbuf>0</sndbuf></tune>]
            [<link state='up|down'/>]
            [<bandwidth>
              [<inbound average="int" [burst="int"]  [peak="int"]/>]
              [<outbound average="int" [burst="int"]  [peak="int"]/>]
             </bandwidth>]
        </interface>
        """
        iface = self.createXmlElem('interface', self.device, ['address'])
        iface.appendChildWithArgs('mac', address=self.macAddr)
        iface.appendChildWithArgs('model', type=self.nicModel)
        iface.appendChildWithArgs('source', bridge=self.network)
        if hasattr(self, 'filter'):
            iface.appendChildWithArgs('filterref', filter=self.filter)

        if hasattr(self, 'linkActive'):
            iface.appendChildWithArgs('link', state='up'
                                      if utils.tobool(self.linkActive)
                                      else 'down')

        if hasattr(self, 'bootOrder'):
            iface.appendChildWithArgs('boot', order=self.bootOrder)

        if self.driver:
            iface.appendChildWithArgs('driver', **self.driver)

        if self.sndbufParam:
            tune = iface.appendChildWithArgs('tune')
            tune.appendChildWithArgs('sndbuf', text=self.sndbufParam)

        if hasattr(self, 'specParams'):
            if 'inbound' in self.specParams or 'outbound' in self.specParams:
                iface.appendChild(self.paramsToBandwidthXML(self.specParams))
        return iface

    def paramsToBandwidthXML(self, specParams, oldBandwidth=None):
        """Returns a valid libvirt xml dom element object."""
        bandwidth = self.createXmlElem('bandwidth', None)
        old = {} if oldBandwidth is None else dict(
            (elem.nodeName, elem) for elem in oldBandwidth.childNodes)
        for key in ('inbound', 'outbound'):
            elem = specParams.get(key)
            if elem is None:  # Use the old setting if present
                if key in old:
                    bandwidth.appendChild(old[key])
            elif elem:
                # Convert the values to string for adding them to the XML def
                attrs = dict((key, str(value)) for key, value in elem.items())
                bandwidth.appendChildWithArgs(key, **attrs)
        return bandwidth
