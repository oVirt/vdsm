# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division


from vdsm.common import xmlutils
from vdsm.virt import displaynetwork
from vdsm.virt import libvirtnetwork
from vdsm.virt import utils
from vdsm.virt import vmxml

from . import hwclass


def display_info(domain):
    def info(gxml):
        listen = vmxml.find_first(gxml, 'listen')
        display_ip = listen.attrib.get('address', '0')
        return {
            'type': vmxml.attr(gxml, 'type'),
            'port': vmxml.attr(gxml, 'port'),
            'tlsPort': vmxml.attr(gxml, 'tlsPort'),
            'ipAddress': display_ip,
        }
    return [info(gxml) for gxml in domain.get_device_elements('graphics')]


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


def _is_feature_flag_enabled(dev, node, attr):
    value = vmxml.find_attr(dev, node, attr)
    if value is not None and value.lower() == 'no':
        return False
    else:
        return True


def is_vnc_secure(vmParams, log):
    """
    This function checks if VNC is not mis-configured to offer insecure,
    free-for-all access. The engine can send the XML with empty password,
    but it's acceptable IFF qemu uses SASL as the authentication mechanism.

    is_vnc_secure returns False in such case (i.e. no password and no SASL),
    otherwise VNC connection is considered secure.
    """
    parsed = xmlutils.fromstring(vmParams['xml'])
    graphics = vmxml.find_all(parsed, 'graphics')
    for g in graphics:
        if vmxml.attr(g, 'type') == 'vnc':
            # When the XML does not contain 'passwordValidTo' attribute
            # this is a way to say 'don't use password auth'.
            no_password_auth = vmxml.attr(g, 'passwdValidTo') == ''
            if no_password_auth and not utils.sasl_enabled():
                log.warning("VNC not secure: passwdValidTo empty or missing"
                            " and SASL not configured")
                return False
    return True


def reset_password(dev_xml):
    """
    Invalidate password in the given <graphics> element.

    :param dev_xml: <graphics> element to reset the password in
    :type dev_xml: xml.tree.ElementTree.Element
    """
    attrs = dev_xml.attrib
    attrs['passwd'] = '*****'
    attrs['passwdValidTo'] = '1970-01-01T00:00:01'


class Graphics(object):

    def __init__(self, device_dom, vm_id):
        self._dom = device_dom
        self._vm_id = vm_id

    @property
    def device(self):
        return xmlutils.tostring(self._dom, pretty=True)

    def setup(self):
        display_network = self._display_network()
        if display_network is not None:
            displaynetwork.create_network(display_network, self._vm_id)

    def teardown(self):
        display_network = self._display_network()
        if display_network is not None:
            displaynetwork.delete_network(display_network, self._vm_id)

    def _display_network(self):
        listen = vmxml.find_first(self._dom, 'listen')
        if listen.attrib.get('type') == 'network':
            xml_display_network = listen.attrib.get('network')
            if xml_display_network:
                return libvirtnetwork.netname_l2o(xml_display_network)
        return None
