# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import logging
import xml.etree.ElementTree as etree

from vdsm.virt import xmlconstants


_UNSPECIFIED = object()


class NotFound(Exception):
    """
    Raised when vmxml helpers can't find some requested entity.
    """
    pass


def find_all(element, tag_):
    """
    Return an iterator over all DOM elements with given `tag`.

    `element` may is included in the result if it is of `tag`.

    :param element: DOM element to be searched for given `tag`
    :type element: DOM element
    :param tag: tag to look for
    :type tag: basestring
    :returns: all elements with given `tag`
    :rtype: sequence of DOM elements
    """
    if tag(element) == tag_:
        yield element
    for elt in element.findall('.//' + tag_):
        yield elt


def find_first(element, tag, default=_UNSPECIFIED):
    """
    Find the first DOM element with the given tag.

    :param element: DOM element to be searched for given `tag`
    :type element: DOM element
    :param tag: tag to look for
    :type tag: basestring
    :param default: any object to return when no element with `tag` is found;
      if not given then `NotFound` is raised in such a case
    :returns: the matching element or default
    :raises: NotFound -- when no element with `tag` is found and `default` is
      not given
    """
    try:
        return next(find_all(element, tag))
    except StopIteration:
        if default is _UNSPECIFIED:
            raise NotFound((element, tag,))
        else:
            return default


def find_attr(element, tag, attribute):
    """
    Find `attribute` value of the first DOM element with `tag`.

    :param element: DOM element to be searched for given `tag`
    :type element: DOM element
    :param tag: tag to look for
    :type tag: basestring
    :param attribute: attribute name to look for
    :type attribute: basestring
    :returns: the attribute value or an empty string if no element with given
      tag is found or the found element doesn't contain `attribute`
    :rtype: basestring
    """
    try:
        subelement = find_first(element, tag)
    except NotFound:
        return ''
    return attr(subelement, attribute)


def tag(element):
    """
    Return tag of the given DOM element.

    :param element: element to get the tag of
    :type element: DOM element
    :returns: tag of the element
    :rtype: basestring
    """
    return element.tag


def attr(element, attribute):
    """
    Return attribute values of `element`.

    :param element: the element to look the attributes in
    :type element: DOM element
    :param attribute: attribute name to look for
    :type attribute: basestring
    :returns: the corresponding attribute value or empty string (if `attribute`
      is not present)
    :rtype: basestring
    """
    # etree returns unicodes, except for empty strings.
    return element.get(attribute, '')


def set_attr(element, attribute, value):
    """
    Set `attribute` of `element` to `value`.

    :param element: the element to change the attribute in
    :type element: DOM element
    :param attribute: attribute name
    :type attribute: basestring
    :param value: new value of the attribute
    :type value: basestring
    """
    element.set(attribute, value)


def text(element):
    """
    Return text of the given DOM element.

    :param element: element to get the text from
    :type element: DOM element
    :returns: text of the element (empty string if it the element doesn't
      contain any text)
    :rtype: basestring
    """
    return element.text or ''


def children(element, tag=None):
    """
    Return direct subelements of `element`.

    :param element: element to get the children from
    :type element: DOM element
    :param tag: if given then only children with this tag are returned
    :type tag: basestring
    :returns: children of `element`, optionally filtered by `tag`
    :rtype: iterator providing the selected children

    """
    if tag is None:
        return iter(element)
    else:
        return element.iterfind('./' + tag)


def append_child(element, child=None, etree_child=None):
    """
    Add child element to `element`.

    :param element: element to add the child to
    :type element: DOM element
    :param child: child element to add to `element`
    :type child: vmxml.Element object
    :param etree_child: child element to add to `element`
    :type child: etree.Element object

    """
    if child is not None and etree_child is None:
        element.append(child._elem)
    elif child is None and etree_child is not None:
        element.append(etree_child)
    else:
        raise RuntimeError(
            'append_child invoked with child=%r etree_child=%r' % (
                child, etree_child))


def remove_child(element, child):
    """
    Remove child element from `element`.

    :param element: element to add the child to
    :type element: DOM element
    :param child: child element to remove from `element`
    :type child: DOM element

    """
    element.remove(child)


def replace_first_child(element, new_child):
    """
    Replace the first child of `element` with `new_child`.

    :param element: element to replace the child in
    :type element: DOM element
    :param new_child: new child element to insert to `element`
    :type new_child: DOM element

    """
    old_child = next(iter(element))
    element.remove(old_child)
    element.insert(0, new_child)


def has_channel(domXML, name):
    domObj = etree.fromstring(domXML)
    devices = domObj.findall('devices')

    if len(devices) == 1:
        for chan in devices[0].findall('channel'):
            targets = chan.findall('target')
            if len(targets) == 1:
                if targets[0].attrib['name'] == name:
                    return True

    return False


def has_vdsm_metadata(domXML):
    domObj = etree.fromstring(domXML)
    metadata = domObj.findall('metadata')
    nsdict = {
        xmlconstants.METADATA_VM_VDSM_PREFIX:
        xmlconstants.METADATA_VM_VDSM_URI
    }
    vdsmtag = (
        xmlconstants.METADATA_VM_VDSM_PREFIX + ':' +
        xmlconstants.METADATA_VM_VDSM_ELEMENT
    )
    for md in metadata:
        if len(md.findall(vdsmtag, nsdict)) > 0:
            return True
    return False


def device_address(device_xml, index=0):
    """
    Obtain device's address from libvirt
    """
    address_element = list(find_all(device_xml, 'address'))[index]
    return parse_address_element(address_element)


def parse_address_element(address_element):
    """
    Parse address to create proper dictionary.
    Libvirt device's address definition is:
    PCI = {'type':'pci', 'domain':'0x0000', 'bus':'0x00',
           'slot':'0x0c', 'function':'0x0'}
    IDE = {'type':'drive', 'controller':'0', 'bus':'0', 'unit':'0'}
    """
    return {
        key.strip(): value.strip()
        for key, value in address_element.attrib.items()
    }


class Device(object):
    # since we're inheriting all VM devices from this class, __slots__ must
    # be initialized here in order to avoid __dict__ creation
    __slots__ = ()

    def createXmlElem(self, elemType, deviceType, attributes=()):
        """
        Create domxml device element according to passed in params
        """
        elemAttrs = {}
        element = Element(elemType)

        if deviceType:
            elemAttrs['type'] = deviceType

        for attrName in attributes:
            if not hasattr(self, attrName):
                continue

            attr = getattr(self, attrName)
            if attr is None:
                log = logging.getLogger('devel')
                log.debug("Attribute '%s' of '%s' device element '%s' is None",
                          attrName, deviceType, elemType)
                continue

            if isinstance(attr, dict):
                element.appendChildWithArgs(attrName, **attr)
            else:
                elemAttrs[attrName] = attr

        element.setAttrs(**elemAttrs)
        return element


class Element(object):

    def __init__(self, tagName, text=None, namespace=None, namespace_uri=None,
                 **attrs):
        if namespace_uri is not None:
            tagName = '{%s}%s' % (namespace_uri, tagName,)
            if namespace is not None:
                etree.register_namespace(namespace, namespace_uri)
        self._elem = etree.Element(tagName)
        self.setAttrs(**attrs)
        if text is not None:
            self.appendTextNode(text)

    def __getattr__(self, name):
        return getattr(self._elem, name)

    def __len__(self):
        return len(self._elem)

    def __iter__(self):
        return iter(self._elem)

    def setAttrs(self, **attrs):
        for attrName, attrValue in attrs.items():
            self._elem.set(attrName, attrValue)

    def setAttr(self, attrName, attrValue):
        self._elem.set(attrName, attrValue)

    def appendTextNode(self, text):
        self._elem.text = text

    def appendChild(self, element=None, etree_element=None):
        append_child(self._elem, element, etree_element)

    def appendChildWithArgs(self, childName, text=None, **attrs):
        child = Element(childName, text, **attrs)
        append_child(self._elem, child)
        return child
