#
# Copyright 2017 Red Hat, Inc.
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

"""
This module allows to store and retrieve key/value pairs into the etree
representation of a libvirt domain XML. Each set of key/value pairs will be
stored under one first-level child of the metadata. Example:

  <metadata>
    <group1>
      <a>1</a>
      <b>2</b>
    </group1>
    <group2>
      <c>3</c>
      <d>4</d>
    </group2>
  <metadata>

The key/value pairs must comply with those requirements:
- keys must be python basestrings
- values must be one of: basestring, int, float
- containers are not supported values; the metadata
  namespace is flat, and you cannot nest objects.
- partial updates are forbidden. You must overwrite all the key/value
  pairs in a given set (hereafter referred as 'group') at the same time.

The flow is:
1. read the metadata using this module
2. update the data you need to work with
3. send back the metadata using this module
"""

from contextlib import contextmanager
import xml.etree.ElementTree as ET

import libvirt
import six

from vdsm.virt import vmxml


class Error(Exception):
    """
    Generic metadata error
    """


class UnsupportedType(Error):
    """
    Unsupported python type. Supported python types are:
    * ints
    * floats
    * string
    """


class Metadata(object):
    """
    Use this class to load or dump a group (see the module docstring) from
    or to a metadata element.
    Optionally handles the XML namespaces. You will need the namespace
    handling when building XML for the VM startup; when updating the
    metadata, libvirt will take care of that.
    See also the docstring of the `create` function.
    """

    def __init__(self, namespace=None, namespace_uri=None):
        """
        :param namespace: namespace to use
        :type namespace: text string
        :param namespace_uri: URI of the namespace to use
        :type namespace_uri: text string
        """
        self._namespace = namespace
        self._namespace_uri = namespace_uri
        self._prefix = None
        if namespace is not None:
            ET.register_namespace(namespace, namespace_uri)
            self._prefix = '{%s}' % self._namespace_uri

    def load(self, elem):
        """
        Load the content of the given metadata element `elem`
        into a python object, trying to recover the correct types.
        To recover the types, this function relies on the element attributes
        added by the `dump` method. Without them, the function will
        still load the content, but everything will be a string.
        Example:

        <example>
            <a>some value</a>
            <b type="int">1</b>
        </example>

        elem = vmxml.parse_xml(...)

        md = Metadata()
        md.load(elem) -> {'a': 'some value', 'b': 1}

        :param elem: root of the ElementTree to load
        :type elem: ElementTree.Element
        :returns: content of the group
        :rtype: dict of key/value pairs. See the module docstring for types
        """
        values = {}
        for child in elem:
            key, val = _elem_to_keyvalue(child)
            values[self._strip_ns(key)] = val
        return values

    def dump(self, name, **kwargs):
        """
        Dump the given arguments into the `name` metadata element.
        This function transparently adds the type hints as element attributes,
        so `load` can restore them.

        Example:

        md = Metadata()
        md.dump('test', bar=42) -> elem

        vmxml.format_xml(elem) ->

        <test>
          <bar type="int">42</bar>
        </test>

        :param name: group to put in the metadata
        :type name: text string
        :param namespace: namespace to use
        :type namespace: text string
        :param namespace_uri: URI of the namespace to use
        :type namespace_uri: text string
        :return: the corresponding element
        :rtype: ElementTree.Element

        kwargs: stored as subelements
        """
        elem = ET.Element(self._add_ns(name))
        for key, value in kwargs.items():
            _keyvalue_to_elem(self._add_ns(key), value, elem)
        return elem

    def _add_ns(self, tag):
        """
        Decorate the given tag with the namespace, if used
        """
        return (self._prefix or '') + tag

    def _strip_ns(self, tag):
        """
        Remove the namespace from the given tag
        """
        return tag.replace(self._prefix, '') if self._prefix else tag


def create(name, namespace, namespace_uri, **kwargs):
    """
    Create one `name` element.
    Use this function to initialize one empty metadata element,
    at XML creation time.

    Example:

    metadata.create('vm', 'ovirt-vm', 'http://ovirt.org/vm/1.0',
                    version=4.2) -> elem

    vmxml.format_xml(elem) ->

    <ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
      <ovirt-vm:version type="float">4.2</ovirt-vm:version>
    </ovirt-vm:vm>

    :param name: group to put in the metadata
    :type name: text string
    :param namespace: namespace to use
    :type namespace: text string
    :param namespace_uri: URI of the namespace to use
    :type namespace_uri: text string
    :return: the corresponding element
    :rtype: ElementTree.Element

    kwargs: stored as subelements
    """
    # here we must add the namespaces ourselves
    metadata_obj = Metadata(namespace, namespace_uri)
    return metadata_obj.dump(name, **kwargs)


@contextmanager
def domain(dom, name, namespace, namespace_uri):
    """
    Helper context manager to simplify the get the instance of Metadata
    from a libvirt Domain object.

    Example:

    let's start with
    dom.metadata() -> <vm/>

    let's run this code
    with metadata.domain(dom, 'vm', 'ovirt-vm',
                         'http://ovirt.org/vm/1.0')
    ) as vm:
        vm['my_awesome_key'] = some_awesome_value()  # returns 42

    now we will have
    dom.metadata() ->
    <vm>
      <my_awesome_key type="int">42</my_awesome_key>
    </vm>

    but if you look in the domain XML (e.g. virsh dumpxml) you will
    have, courtesy of libvirt:

    <metadata>
      <ovirt-vm:vm xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
        <ovirt-vm:my_awesome_key type="int">42</ovirt-vm:my_awesome_key>
      </ovirt-vm:vm>
    </metadata>

    :param dom: domain to access
    :type dom: libvirt.Domain
    :param name: metadata group to access
    :type name: text string
    :param namespace: metadata namespace to use
    :type namespace: text string
    :param namespace_uri: metadata namespace URI to use
    :type namespace_uri: text string
    """
    with _domain_xml(dom, name, namespace, namespace_uri) as metadata_xml:
        # we DO NOT want to handle namespaces ourselves; libvirt does
        # it automatically for us.
        metadata_obj = Metadata()
        content = metadata_obj.load(metadata_xml.get())
        yield content
        metadata_xml.set(metadata_obj.dump(name, **content))


class _XMLWrapper(object):
    def __init__(self, metadata_xml):
        self._xml = metadata_xml

    @property
    def xml(self):
        return self._xml

    def get(self):
        return vmxml.parse_xml(self._xml)

    def set(self, elem):
        self._xml = vmxml.format_xml(elem)


@contextmanager
def _domain_xml(dom, tag, namespace, namespace_uri):
    metadata_xml = "<{tag}/>".format(tag=tag)
    try:
        metadata_xml = dom.metadata(libvirt.VIR_DOMAIN_METADATA_ELEMENT,
                                    namespace_uri,
                                    0)

    except libvirt.libvirtError as e:
        if e.get_error_code() != libvirt.VIR_ERR_NO_DOMAIN_METADATA:
            raise

    xml_wrap = _XMLWrapper(metadata_xml)
    yield xml_wrap

    dom.setMetadata(libvirt.VIR_DOMAIN_METADATA_ELEMENT,
                    xml_wrap.xml,
                    namespace,
                    namespace_uri,
                    0)


def _elem_to_keyvalue(elem):
    key = elem.tag
    value = elem.text
    data_type = elem.attrib.get('type')
    if data_type is not None:
        if data_type == 'int':
            value = int(value)
        elif data_type == 'float':
            value = float(value)
        # elif data_type == 'str': do nothing
    return key, value


def _keyvalue_to_elem(key, value, elem):
    subelem = ET.SubElement(elem, key)
    if isinstance(value, int):
        subelem.attrib['type'] = 'int'
    elif isinstance(value, float):
        subelem.attrib['type'] = 'float'
    elif isinstance(value, six.string_types):
        pass
    else:
        raise UnsupportedType(value)
    subelem.text = str(value)
    return subelem
