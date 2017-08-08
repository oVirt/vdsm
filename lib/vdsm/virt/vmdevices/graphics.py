#
# Copyright 2008-2017 Red Hat, Inc.
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
# pylint: disable=no-member

from __future__ import absolute_import

import libvirt

from vdsm import supervdsm
from vdsm.common import conv
from vdsm.network import api as net_api
from vdsm.virt import libvirtnetwork
from vdsm.config import config
from vdsm.virt import vmxml

from . import hwclass
from .core import Base
from .core import update_device_params, find_device_type

import re

LIBVIRT_PORT_AUTOSELECT = '-1'


_LEGACY_MAP = {
    'keyboardLayout': 'keyMap',
    'displayNetwork': 'displayNetwork',
    'spiceSecureChannels': 'spiceSecureChannels',
    'copyPasteEnable': 'copyPasteEnable',
    'fileTransferEnable': 'fileTransferEnable',
}


class Graphics(Base):

    SPICE_CHANNEL_NAMES = (
        'main', 'display', 'inputs', 'cursor', 'playback',
        'record', 'smartcard', 'usbredir')

    __slots__ = ('port', 'tlsPort',)

    def __init__(self, log, **kwargs):
        super(Graphics, self).__init__(log, **kwargs)
        self.port = LIBVIRT_PORT_AUTOSELECT
        self.tlsPort = LIBVIRT_PORT_AUTOSELECT

    def setup(self):
        display_network = self.specParams['displayNetwork']
        if display_network:
            libvirtnetwork.create_network(display_network, self.vmid)
            display_ip = _getNetworkIp(display_network)
        else:
            display_ip = '0'
        self.specParams['displayIp'] = display_ip

    def teardown(self):
        display_network = self.specParams['displayNetwork']
        if display_network:
            libvirtnetwork.delete_network(display_network, self.vmid)

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

    @classmethod
    def from_xml_tree(cls, log, dev, meta):
        # we parse `port` and `tlsPort` but we don't honour them to be
        # consistent with __init__ which will always set them to
        # AUTOSELECT (-1)
        params = {
            'device': find_device_type(dev),
            'type': find_device_type(dev),
        }
        update_device_params(params, dev, attrs=('port', 'tlsPort'))
        params['specParams'] = _make_spec_params(dev, meta)
        params['vmid'] = meta['vmid']
        return cls(log, **params)

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
            'autoport': 'yes',
        }
        if config.getboolean('vars', 'ssl'):
            graphicsAttrs['defaultMode'] = 'secure'
        # the default, 'any', has automatic fallback to
        # insecure mode, so works with ssl off.

        if self.device == 'spice':
            graphicsAttrs['tlsPort'] = self.tlsPort

        self._setPasswd(graphicsAttrs)

        if 'keyMap' in self.specParams:
            graphicsAttrs['keymap'] = self.specParams['keyMap']

        graphics = vmxml.Element('graphics', **graphicsAttrs)

        if not conv.tobool(self.specParams.get('copyPasteEnable', True)):
            clipboard = vmxml.Element('clipboard', copypaste='no')
            graphics.appendChild(clipboard)

        if not conv.tobool(
                self.specParams.get('fileTransferEnable', True)):
            filetransfer = vmxml.Element('filetransfer', enable='no')
            graphics.appendChild(filetransfer)

        # This list could be dropped in 4.1. We should keep only
        # the default mode, which is both simpler and safer.
        if (self.device == 'spice' and
           'spiceSecureChannels' in self.specParams):
            for chan in self._getSpiceChannels():
                graphics.appendChildWithArgs('channel', name=chan,
                                             mode='secure')

        # For the listen type IP to be used, the display network must be OVS.
        # We assume that the cluster in which the host operates is OVS enabled
        # and all other hosts in the cluster have the migration hook installed.
        # The migration hook is responsible to convert ip to net and vice versa
        display_network = self.specParams['displayNetwork']
        display_ip = self.specParams.get('displayIp', '0')
        if (display_network and display_ip != '0' and
                supervdsm.getProxy().ovs_bridge(display_network)):
            graphics.appendChildWithArgs(
                'listen', type='address', address=display_ip)
        elif display_network:
            graphics.appendChildWithArgs(
                'listen', type='network',
                network=libvirtnetwork.netname_o2l(display_network))
        else:
            graphics.setAttrs(listen='0')

        return graphics

    def _setPasswd(self, attrs):
        attrs['passwd'] = '*****'
        attrs['passwdValidTo'] = '1970-01-01T00:00:01'

    def setupPassword(self, devXML):
        self._setPasswd(devXML.attrib)

    @classmethod
    def update_device_info(cls, vm, device_conf):
        for gxml in vm.domain.get_device_elements('graphics'):
            port = vmxml.attr(gxml, 'port')
            tlsPort = vmxml.attr(gxml, 'tlsPort')
            graphicsType = vmxml.attr(gxml, 'type')

            for d in device_conf:
                if d.device == graphicsType:
                    if port:
                        d.port = port
                    if tlsPort:
                        d.tlsPort = tlsPort
                    break

            for dev in vm.conf['devices']:
                if (dev.get('type') == hwclass.GRAPHICS and
                        dev.get('device') == graphicsType):
                    if port:
                        dev['port'] = port
                    if tlsPort:
                        dev['tlsPort'] = tlsPort
                    break

    def get_extra_xmls(self):
        if self.device == 'spice':
            yield self.getSpiceVmcChannelsXML()


def isSupportedDisplayType(vmParams):
    display = vmParams.get('display')
    if display is not None:
        if display not in ('vnc', 'qxl', 'qxlnc'):
            return False
    # else:
    # either headless VM or modern Engine which just sends the
    # graphics device(s). Go ahead anyway.

    for dev in vmParams.get('devices', ()):
        if dev['type'] == hwclass.GRAPHICS:
            if dev['device'] not in ('spice', 'vnc'):
                return False

    # either no graphics device or correct graphic device(s)
    return True


def makeSpecParams(conf):
    return dict((newName, conf[oldName])
                for oldName, newName in _LEGACY_MAP.iteritems()
                if oldName in conf)


def _getNetworkIp(network):
    try:
        nets = libvirtnetwork.networks()
        # On a legacy based network, the device is the iface specified in the
        # network report (supporting real bridgeless networks).
        # In case the report or the iface key is missing,
        # the device is defaulted to the network name (i.e. northbound port).
        device = (nets[network].get('iface', network)
                  if network in nets else network)
        ip, _, _, _ = net_api.ip_addrs_info(device)
    except (libvirt.libvirtError, KeyError, IndexError):
        ip = '0'
    finally:
        if ip == '':
            ip = '0'
    return ip


def fixDisplayNetworks(xml_str):
    networks = set(re.findall('(?<=DISPLAY-NETWORK:)[\w:-]+', xml_str))
    for network in networks:
        xml_str = xml_str.replace('DISPLAY-NETWORK:' + network,
                                  libvirtnetwork.netname_o2l(network))
    return xml_str


def _make_spec_params(dev, meta):
    spec_params = {
        'copyPasteEnable': _is_feature_flag_enabled(
            dev, 'clipboard', 'copypaste'
        ),
        'fileTransferEnable': _is_feature_flag_enabled(
            dev, 'filetransfer', 'enable'
        ),
    }
    key_map = dev.attrib.get('keymap')
    if key_map:
        spec_params['keyMap'] = key_map
    # we need some overrides to undo legacy defaults
    display_network = meta.get('display_network')
    if display_network:
        spec_params['displayNetwork'] = display_network
    listen = vmxml.find_first(dev, 'listen')
    if listen.attrib.get('type') == 'network':
        xml_display_network = listen.attrib.get('network')
        if xml_display_network:
            spec_params['displayNetwork'] = libvirtnetwork.netname_l2o(
                xml_display_network
            )
    elif listen.attrib.get('type') == 'address':
        spec_params['displayIp'] = listen.attrib.get('address', '0')
    return spec_params


def _is_feature_flag_enabled(dev, node, attr):
    value = vmxml.find_attr(dev, node, attr)
    if value is not None and value.lower() == 'no':
        return False
    else:
        return True
