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

import sys
import xml.dom
import xml.dom.minidom


def all_devices(domXML):
    domObj = xml.dom.minidom.parseString(domXML)
    devices = domObj.childNodes[0].getElementsByTagName('devices')[0]

    for deviceXML in devices.childNodes:
        if deviceXML.nodeType == xml.dom.Node.ELEMENT_NODE:
            yield deviceXML


def filter_devices_with_alias(devices):
    for deviceXML in devices:
        aliasElement = deviceXML.getElementsByTagName('alias')
        if aliasElement:
            alias = aliasElement[0].getAttribute('name')
            yield deviceXML, alias


class Element(object):

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
        child = Element(childName, text, **attrs)
        self._elem.appendChild(child)
        return child


if sys.version_info[:2] == (2, 6):
    # A little unrelated hack to make xml.dom.minidom.Document.toprettyxml()
    # not wrap Text node with whitespace.
    # reported upstream in http://bugs.python.org/issue4147
    # fixed in python 2.7 and python >= 3.2
    def __hacked_writexml(self, writer, indent="", addindent="", newl=""):

        # copied from xml.dom.minidom.Element.writexml and hacked not to wrap
        # Text nodes with whitespace.

        # indent = current indentation
        # addindent = indentation to add to higher levels
        # newl = newline string
        writer.write(indent + "<" + self.tagName)

        attrs = self._get_attributes()
        a_names = attrs.keys()
        a_names.sort()

        for a_name in a_names:
            writer.write(" %s=\"" % a_name)
            # _write_data(writer, attrs[a_name].value) # replaced
            xml.dom.minidom._write_data(writer, attrs[a_name].value)
            writer.write("\"")
        if self.childNodes:
            # added special handling of Text nodes
            if (len(self.childNodes) == 1 and
                    isinstance(self.childNodes[0], xml.dom.minidom.Text)):
                writer.write(">")
                self.childNodes[0].writexml(writer)
                writer.write("</%s>%s" % (self.tagName, newl))
            else:
                writer.write(">%s" % (newl))
                for node in self.childNodes:
                    node.writexml(writer, indent + addindent, addindent, newl)
                writer.write("%s</%s>%s" % (indent, self.tagName, newl))
        else:
            writer.write("/>%s" % (newl))

    xml.dom.minidom.Element.writexml = __hacked_writexml
