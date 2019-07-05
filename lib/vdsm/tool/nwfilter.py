# Copyright 2012-2017 Red Hat, Inc.
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


from __future__ import absolute_import
from __future__ import division
import logging

import libvirt

from vdsm.common import libvirtconnection
from . import expose, ExtraArgsError


@expose('nwfilter')
def main(*args):
    """
    nwfilter
    Defines network filters on libvirt
    """
    if len(args) > 1:
        raise ExtraArgsError()

    conn = libvirtconnection.get(None, False)
    NoMacSpoofingFilter(conn).define()


class NwFilter(object):
    """
    Base class for custom network filters
    """

    def __init__(self, name, connection):
        self.filterName = name
        self.connection = connection

    def _get_libvirt_filter(self):
        try:
            return self.connection.nwfilterLookupByName(self.filterName)
        except libvirt.libvirtError:
            return None

    def _get_libvirt_uuid(self):
        libvirt_filter = self._get_libvirt_filter()
        if libvirt_filter:
            return libvirt_filter.UUIDString()

    def _get_uuid_xml(self):
        libvirt_uuid = self._get_libvirt_uuid()
        if libvirt_uuid:
            return '<uuid>{}</uuid>'.format(libvirt_uuid)
        return ''

    def get_xml_template(self):
        raise NotImplementedError("Should have implemented this")

    def build_xml(self):
        return self.get_xml_template().format(
            name=self.filterName,
            uuid_xml=self._get_uuid_xml()
        )

    def define(self):
        """
        define vdsm network filter on libvirt to control VM traffic
        """
        libvirt_filter = self.connection.nwfilterDefineXML(self.build_xml())
        logging.debug("Filter %s was defined", libvirt_filter.name())


class NoMacSpoofingFilter(NwFilter):
    """
    Class defines the vdsm-no-mac-spoofing filter which is comprised of
    two libvirt OOB filters: no-mac-spoofing and no-arp-mac-spoofing
    """

    def __init__(self, connection):
        NwFilter.__init__(self, 'vdsm-no-mac-spoofing', connection)

    def get_xml_template(self):
        return '''<filter name='{name}' chain='root'>
                      {uuid_xml}
                      <filterref filter='no-mac-spoofing'/>
                      <filterref filter='no-arp-mac-spoofing'/>
                  </filter> '''
