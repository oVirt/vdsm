#! /usr/bin/python
# Copyright 2012 Red Hat, Inc.
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

from vdsm import libvirtconnection


def main():
    """
    Defines network filters on libvirt
    """
    conn = libvirtconnection.get()
    NoMacSpoofingFilter().defineNwFilter(conn)
    conn.close()


class NwFilter(object):
    """
    Base class for custom network filters
    """

    def __init__(self, name):
        self.filterName = name

    def getFilterXml(self):
        raise NotImplementedError("Should have implemented this")

    def buildFilterXml(self):
        return self.getFilterXml() % self.filterName

    def defineNwFilter(self, conn):
        """
        define vdsm network filter on libvirt to control VM traffic
        """

        try:
            conn.nwfilterLookupByName(self.filterName).undefine()
        except libvirt.libvirtError:
            # Ignore failure if filter isn't exists or if failed to remove.
            # Failure might occur when attempting to remove a filter which
            # is being used by running VMs
            pass

        nwFilter = conn.nwfilterDefineXML(self.buildFilterXml())
        logging.debug("Filter %s was defined" % nwFilter.name())


class NoMacSpoofingFilter(NwFilter):
    """
    Class defines the vdsm-no-mac-spoofing filter which is comprised of
    two libvirt OOB filters: no-mac-spoofing and no-arp-mac-spoofing
    """

    def __init__(self):
        NwFilter.__init__(self, 'vdsm-no-mac-spoofing')

    def getFilterXml(self):
        return '''<filter name='%s' chain='root'>
                      <filterref filter='no-mac-spoofing'/>
                      <filterref filter='no-arp-mac-spoofing'/>
                  </filter> '''

if __name__ == '__main__':
    main()
