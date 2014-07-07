#
# Copyright 2014 Red Hat, Inc.
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

"""
shared utilities and common code for the virt package
"""

import xml


def isVdsmImage(drive):
    """
    Tell if drive looks like a vdsm image

    :param drive: drive to check
    :type drive: dict or vm.Drive
    :return: bool
    """
    required = ('domainID', 'imageID', 'poolID', 'volumeID')
    return all(k in drive for k in required)


class XMLElement(object):

    def __init__(self, tagName, text=None, **attrs):
        self._elem = xml.dom.minidom.Document().createElement(tagName)
        self.setAttrs(**attrs)
        if text is not None:
            self.appendTextNode(text)

    def __getattr__(self, name):
        return getattr(self._elem, name)

    def setAttrs(self, **attrs):
        for attrName, attrValue in attrs.iteritems():
            self._elem.setAttribute(attrName, attrValue)

    def appendTextNode(self, text):
        textNode = xml.dom.minidom.Document().createTextNode(text)
        self._elem.appendChild(textNode)

    def appendChild(self, element):
        self._elem.appendChild(element)

    def appendChildWithArgs(self, childName, text=None, **attrs):
        child = XMLElement(childName, text, **attrs)
        self._elem.appendChild(child)
        return child
