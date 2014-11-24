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

import logging

import libvirt

from vdsm import netinfo
from vdsm import utils
from vdsm.config import config

from .. import vmxml

from . import hwclass
from .core import Base


LIBVIRT_PORT_AUTOSELECT = '-1'


_LEGACY_MAP = {
    'keyboardLayout': 'keyMap',
    'spiceDisableTicketing': 'disableTicketing',
    'displayNetwork': 'displayNetwork',
    'spiceSecureChannels': 'spiceSecureChannels',
    'copyPasteEnable': 'copyPasteEnable'}


class Graphics(Base):

    SPICE_CHANNEL_NAMES = (
        'main', 'display', 'inputs', 'cursor', 'playback',
        'record', 'smartcard', 'usbredir')

    __slots__ = ('device', 'port', 'tlsPort')

    def __init__(self, conf, log, **kwargs):
        super(Graphics, self).__init__(conf, log, **kwargs)
        self.port = LIBVIRT_PORT_AUTOSELECT
        self.tlsPort = LIBVIRT_PORT_AUTOSELECT
        self.specParams['displayIp'] = _getNetworkIp(
            self.specParams.get('displayNetwork'))

    def getSpiceVmcChannelsXML(self):
        vmc = vmxml.Element('channel', type='spicevmc')
        vmc.appendChildWithArgs('target', type='virtio',
                                name='com.redhat.spice.0')
        return vmc

    def _getSpiceChannels(self):
        for name in self.specParams['spiceSecureChannels'].split(','):
            if name in Graphics.SPICE_CHANNEL_NAMES:
                yield name
            elif (name[0] == 's' and name[1:] in
                  Graphics.SPICE_CHANNEL_NAMES):
                # legacy, deprecated channel names
                yield name[1:]
            else:
                self.log.error('unsupported spice channel name "%s"', name)

    def getXML(self):
        """
        Create domxml for a graphics framebuffer.

        <graphics type='spice' port='5900' tlsPort='5901' autoport='yes'
                  listen='0' keymap='en-us'
                  passwdValidTo='1970-01-01T00:00:01'>
          <listen type='address' address='0'/>
          <clipboard copypaste='no'/>
        </graphics>
        OR
        <graphics type='vnc' port='5900' autoport='yes' listen='0'
                  keymap='en-us' passwdValidTo='1970-01-01T00:00:01'>
          <listen type='address' address='0'/>
        </graphics>

        """

        graphicsAttrs = {
            'type': self.device,
            'port': self.port,
            'autoport': 'yes'}

        if self.device == 'spice':
            graphicsAttrs['tlsPort'] = self.tlsPort

        if not utils.tobool(self.specParams.get('disableTicketing', False)):
            graphicsAttrs['passwd'] = '*****'
            graphicsAttrs['passwdValidTo'] = '1970-01-01T00:00:01'

        if 'keyMap' in self.specParams:
            graphicsAttrs['keymap'] = self.specParams['keyMap']

        graphics = vmxml.Element('graphics', **graphicsAttrs)

        if not utils.tobool(self.specParams.get('copyPasteEnable', True)):
            clipboard = vmxml.Element('clipboard', copypaste='no')
            graphics.appendChild(clipboard)

        if (self.device == 'spice' and
           'spiceSecureChannels' in self.specParams):
            for chan in self._getSpiceChannels():
                graphics.appendChildWithArgs('channel', name=chan,
                                             mode='secure')

        if self.specParams.get('displayNetwork'):
            graphics.appendChildWithArgs('listen', type='network',
                                         network=netinfo.LIBVIRT_NET_PREFIX +
                                         self.specParams.get('displayNetwork'))
        else:
            graphics.setAttrs(listen='0')

        return graphics


def isSupportedDisplayType(vmParams):
    if vmParams.get('display') not in ('vnc', 'qxl', 'qxlnc'):
        return False
    for dev in vmParams.get('devices', ()):
        if dev['type'] == hwclass.GRAPHICS:
            if dev['device'] not in ('spice', 'vnc'):
                return False
    return True


def makeSpecParams(conf):
    return dict((newName, conf[oldName])
                for oldName, newName in _LEGACY_MAP.iteritems()
                if oldName in conf)


def initLegacyConf(conf):
    conf['displayPort'] = LIBVIRT_PORT_AUTOSELECT
    conf['displaySecurePort'] = LIBVIRT_PORT_AUTOSELECT
    conf['displayIp'] = _getNetworkIp(conf.get('displayNetwork'))

    dev = getFirstGraphics(conf)
    if dev:
        # proper graphics device always take precedence
        conf['display'] = 'qxl' if dev['device'] == 'spice' else 'vnc'


def updateLegacyConf(conf):
    dev = getFirstGraphics(conf)
    if 'port' in dev:
        conf['displayPort'] = dev['port']
    if 'tlsPort' in dev:
        conf['displaySecurePort'] = dev['tlsPort']


def getFirstGraphics(conf):
    for dev in conf.get('devices', ()):
        if dev.get('type') == hwclass.GRAPHICS:
            return dev


def _getNetworkIp(network):
    try:
        nets = netinfo.networks()
        device = nets[network].get('iface', network)
        ip, _, _, _ = netinfo.getIpInfo(device)
    except (libvirt.libvirtError, KeyError, IndexError):
        ip = config.get('addresses', 'guests_gateway_ip')
        if ip == '':
            ip = '0'
        logging.info('network %s: using %s', network, ip)
    return ip
