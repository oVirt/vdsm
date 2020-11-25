#
# Copyright 2016 Red Hat, Inc.
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

import copy
import io
import xml.etree.ElementTree as etree

import six


def fromstring(data):
    parser = etree.XMLParser()
    if isinstance(data, six.binary_type):
        parser.feed(data)
    else:
        # ElementTree prefers binary type
        parser.feed(data.encode('utf-8'))
    return parser.close()


def tostring(element, pretty=False):
    if pretty:
        element = copy.deepcopy(element)
        indent(element, 0)
    # amended version of the implementation of tostring()
    # found in python 3.6
    stream = io.BytesIO()
    etree.ElementTree(element).write(
        stream, encoding='utf-8', xml_declaration=True)
    return stream.getvalue().decode('utf-8')


def indent(element, level=0, s="    "):
    """
    Modify element indentation in-place.

    Based on http://effbot.org/zone/element-lib.htm#prettyprint
    """
    i = "\n" + level * s
    if len(element):
        if not element.text or not element.text.strip():
            element.text = i + s
        if not element.tail or not element.tail.strip():
            element.tail = i
        for element in element:
            indent(element, level + 1, s)
        if not element.tail or not element.tail.strip():
            element.tail = i
    else:
        if level and (not element.tail or not element.tail.strip()):
            element.tail = i


def sort_attributes(root):
    """
    Sorts XML attributes in the lexical order. While from semantic point of
    view order of the attributes doesn't matter and shouldn't change behaviour
    of any component using XML, this function can be handy for comparing XMLs.

    Prior to Python 3.8, attributes were ordered lexically by default. This
    behaviour wasn't documented and has changed in Python 3.8. It breaks
    backward compatibility, keeping now attribute order as specified by user.
    See https://bugs.python.org/issue34160

    Taken from https://bugs.python.org/issue34160#msg338102
    """
    for el in root.iter():
        attrib = el.attrib
        if len(attrib) > 1:
            attribs = sorted(attrib.items())
            attrib.clear()
            attrib.update(attribs)
