# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

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
import logging
import operator
import threading
import xml.etree.ElementTree as ET

import libvirt
import six

from vdsm.common import conv
from vdsm.common import errors
from vdsm.common import xmlutils
from vdsm.virt import vmxml
from vdsm.virt import xmlconstants
from vdsm import utils


_CUSTOM = 'custom'
_DEVICE = 'device'

_ADDRESS = 'address'
_AUTH = 'auth'
_CHANGE = 'change'
_HOSTS = 'hosts'
_HOST_INFO = 'hostInfo'
_IO_TUNE = 'ioTune'
_NETWORK = 'network'
_PAYLOAD = 'payload'
_PORT_MIRRORING = 'portMirroring'
_REPLICA = 'diskReplicate'
_SPEC_PARAMS = 'specParams'
_VM_CUSTOM = 'vm_custom'
_VOLUME_CHAIN = 'volumeChain'
_VOLUME_CHAIN_NODE = 'volumeChainNode'
_VOLUME_INFO = 'volumeInfo'
_IGNORED_KEYS = (
    _VOLUME_INFO,
)
_DEVICE_SUBKEYS = (
    _ADDRESS, _AUTH, _CHANGE, _CUSTOM, _HOSTS, _PAYLOAD, _PORT_MIRRORING,
    _REPLICA, _SPEC_PARAMS, _VM_CUSTOM, _VOLUME_CHAIN,
)
_NONEMPTY_KEYS = (_IO_TUNE,) + _DEVICE_SUBKEYS
_LAYERED_KEYS = {
    _HOSTS: _HOST_INFO,
    _VOLUME_CHAIN: _VOLUME_CHAIN_NODE,
}


class Error(errors.Base):
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
    msg = 'Unsupported {self.value} for {self.key}'

    def __init__(self, key, value):
        self.key = key
        self.value = value


class MissingDevice(Error):
    msg = 'Failed to uniquely identify one device using the given attributes'


class Metadata(object):
    """
    Use this class to load or dump a group (see the module docstring) from
    or to a metadata element.
    Optionally handles the XML namespaces. You will need the namespace
    handling when building XML for the VM startup; when updating the
    metadata, libvirt will take care of that.
    See also the docstring of the `create` function.

    Thread safety note:
    Those methods are guaranteed to be thread safe:
    - load()
    - dump()
    - to_xml()

    In a nutshell, the class guarantees that serialization/deserialization
    is thread safe. The calling code is in charge of the atomicity of
    the updates (see methods values(), device(), custom())
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

        elem = xmlutils.fromstring(...)

        md = Metadata()
        md.load(elem) -> {'a': 'some value', 'b': 1}

        Raises:
        - UnsupportedType if the hinted type ('type' attribute) is unsupported
        - ValueError if the value of the attribute can't be converted to the
          hinted type.

        :param elem: root of the ElementTree to load
        :type elem: ElementTree.Element
        :returns: content of the group
        :rtype: dict of key/value pairs. See the module docstring for types
        """
        values = {}
        for child in elem:
            if len(child) > 0:
                # skip not-leaf children: we can't decode them anyway.
                continue
            key, val = _elem_to_keyvalue(child)
            values[self._strip_ns(key)] = val
        return values

    def dump(self, element_name, **kwargs):
        """
        Dump the given arguments into the `element_name` metadata element.
        This function transparently adds the type hints as element attributes,
        so `load` can restore them.

        Example:

        md = Metadata()
        md.dump('test', bar=42) -> elem

        xmlutils.tostring(elem) ->

        <test>
          <bar type="int">42</bar>
        </test>

        :param element_name: group to put in the metadata
        :type element_name: text string
        :return: the corresponding element
        :rtype: ElementTree.Element

        kwargs: stored as subelements, see example above
        """
        elem = ET.Element(self._add_ns(element_name))
        for key, value in sorted(
            kwargs.items(),
            key=operator.itemgetter(0),
        ):
            _keyvalue_to_elem(self._add_ns(key), value, elem)
        return elem

    def make_element(self, tag, parent=None):
        """
        Namespace-aware wrapper to create ET.*Element-s
        """
        if parent is None:
            return ET.Element(self._add_ns(tag))
        else:
            return ET.SubElement(parent, self._add_ns(tag))

    def find(self, elem, tag):
        """
        Namespace-aware wrapper for elem.find()
        """
        return elem.find(self._add_ns(tag))

    def match(self, elem, tag):
        """
        Namespace-aware tag matching helper
        """
        return elem.tag == self._add_ns(tag)

    def findall(self, elem, tag):
        """
        Namespace-aware wrapper for elem.findall()
        """
        for elt in elem.findall(self._add_ns(tag)):
            yield elt

    def dump_sequence(self, element_name, subelement_name, sequence):
        """
        Dump the given sequence into the `element_name` metadata element.
        In contrast with the `dump` method, which builds a map-like structure,
        this method creates a sequence-like structure.
        This function does not transparently add the type hints as element
        attributes.

        Example:

        md = Metadata()
        md.dump_sequence('test', 'item', (bar, baz, 42)) -> elem

        xmlutils.tostring(elem) ->

        <test>
          <item>bar</item>
          <item>baz</item>
          <item>42</item>
        </test>

        :param element_name: group to put in the metadata
        :type element_name: text string
        :param subelement_name: tag of every element forming the sequence
        :type subelement_name: text string
        :param sequence: sequence to encode (dump) as xml elements
        :type sequence: any iterable
        :return: the corresponding element
        :rtype: ElementTree.Element
        """
        elem = ET.Element(self._add_ns(element_name))
        for item in sequence:
            subelem = ET.SubElement(elem, self._add_ns(subelement_name))
            subelem.text = str(item)
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


def replace_device(dst_md, src_md, attrs):
    with dst_md.device(**attrs) as dst_dev_meta:
        with src_md.device(**attrs) as src_dev_meta:
            dst_dev_meta.clear()
            dst_dev_meta.update(src_dev_meta)


def create(name, namespace, namespace_uri, **kwargs):
    """
    Create one `name` element.
    Use this function to initialize one empty metadata element,
    at XML creation time.

    Example:

    metadata.create('vm', 'ovirt-vm', 'http://ovirt.org/vm/1.0',
                    version=4.2) -> elem

    xmlutils.tostring(elem) ->

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


class Descriptor(object):

    _log = logging.getLogger('virt.metadata.Descriptor')

    def __init__(
        self,
        name=xmlconstants.METADATA_VM_VDSM_ELEMENT,
        namespace=xmlconstants.METADATA_VM_VDSM_PREFIX,
        namespace_uri=xmlconstants.METADATA_VM_VDSM_URI
    ):
        """
        Initializes one empty descriptor.

        :param name: metadata group to access
        :type name: text string
        :param namespace: metadata namespace to use
        :type namespace: text string
        :param namespace_uri: metadata namespace URI to use
        :type namespace_uri: text string

        Example:

        given this XML:

        test_xml ->
        <?xml version="1.0" encoding="utf-8"?>
        <domain type="kvm" xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
          <metadata>
            <ovirt-vm:vm>
              <ovirt-vm:version type="float">4.2</ovirt-vm:version>
              <ovirt-vm:custom>
                <ovirt-vm:foo>bar</ovirt-vm:foo>
              </ovirt-vm:custom>
            </ovirt-vm:vm>
          </metadata>
        </domain>

        md_desc = Descriptor.from_xml(
            test_xml, 'vm', 'ovirt-vm', 'http://ovirt.org/vm/1.0'
        )
        with md_desc.values() as vm:
          print(vm)

        will emit
        {
          'version': 4.2,
        }

        print(md_desc.custom())

        will emit
        {
          'foo': 'bar'
        }
        """
        self._lock = threading.Lock()
        self._name = name
        self._namespace = namespace
        self._namespace_uri = namespace_uri
        self._values = {}
        self._custom = {}
        self._devices = []

    def __bool__(self):
        # custom properties may be missing, and that's fine.
        # per-device metadata may be missing too: unusual,
        # but still legitimate.
        with self._lock:
            return (
                bool(self._values) or bool(self._devices) or
                bool(self._custom)
            )

    # pylint: disable=nonzero-method
    def __nonzero__(self):  # TODO: drop when py2 is no longer needed
        return self.__bool__()

    @classmethod
    def from_xml(
        cls,
        xml_str,
        name=xmlconstants.METADATA_VM_VDSM_ELEMENT,
        namespace=xmlconstants.METADATA_VM_VDSM_PREFIX,
        namespace_uri=xmlconstants.METADATA_VM_VDSM_URI
    ):
        """
        Initializes one descriptor given the namespace-prefixed metadata
        snippet. Useful in the VM creation flow, when the
        libvirt Domain is not yet started.

        :param xml_str: domain XML to parse
        :type name: text string
        :param name: metadata group to access
        :type name: text string
        :param namespace: metadata namespace to use
        :type namespace: text string
        :param namespace_uri: metadata namespace URI to use
        :type namespace_uri: text string
        """
        obj = cls(name, namespace, namespace_uri)
        obj._parse_xml(xml_str)
        return obj

    @classmethod
    def from_tree(
        cls,
        root,
        name=xmlconstants.METADATA_VM_VDSM_ELEMENT,
        namespace=xmlconstants.METADATA_VM_VDSM_PREFIX,
        namespace_uri=xmlconstants.METADATA_VM_VDSM_URI
    ):
        """
        Initializes one descriptor given the root Element, obtained
        from xmlutils.fromstring() or similar function.
        Useful for the integration with the DomainDescriptor.

        :param root: root XML Element.
        :type root: DOM element
        :param name: metadata group to access
        :type name: text string
        :param namespace: metadata namespace to use
        :type namespace: text string
        :param namespace_uri: metadata namespace URI to use
        :type namespace_uri: text string
        """
        obj = cls(name, namespace, namespace_uri)
        obj._parse_tree(root)
        return obj

    def load(self, dom):
        """
        Reads the content of the metadata section from the given libvirt
        domain. This will fully overwrite any existing content stored in the
        Descriptor. The data in the libvirt domain is not changed at all.

        :param dom: domain to access
        :type dom: libvirt.Domain
        """
        md_xml = "<{tag}/>".format(tag=self._name)
        try:
            md_xml = dom.metadata(
                libvirt.VIR_DOMAIN_METADATA_ELEMENT,
                self._namespace_uri,
                0
            )
        except libvirt.libvirtError as e:
            if e.get_error_code() != libvirt.VIR_ERR_NO_DOMAIN_METADATA:
                raise
            # else `md_xml` not reassigned, so we will parse empty section
            # and that's exactly what we want.

        self._log.debug(
            'loading metadata for %s: %s', dom.UUIDString(), md_xml)
        self._load(xmlutils.fromstring(md_xml))

    def dump(self, dom):
        """
        Serializes all the content stored in the descriptor, completely
        overwriting the content of the libvirt domain.

        :param dom: domain to access
        :type dom: libvirt.Domain
        """
        md_xml = self._build_xml()
        dom.setMetadata(libvirt.VIR_DOMAIN_METADATA_ELEMENT,
                        md_xml,
                        self._namespace,
                        self._namespace_uri)
        self._log.debug(
            'dumped metadata for %s: %s', dom.UUIDString(), md_xml)

    def to_xml(self):
        """
        Produces the namespace-prefixed XML representation of the full content
        of this Descriptor.

        :rtype: string
        """
        return self._build_xml(self._namespace, self._namespace_uri)

    def to_tree(self):
        """
        Produces a tree of Element representing the full content
        of this Descriptor.

        :rtype: DOM element
        """
        with self._lock:
            return self._build_tree(self._namespace, self._namespace_uri)

    @contextmanager
    def device(self, **kwargs):
        """
        Helper context manager to get and update the metadata of
        a given device.
        Any change performed to the device metadata is not committed
        to the underlying libvirt.Domain until dump() is called.

        :param dom: domain to access
        :type dom: libvirt.Domain

        kwargs: attributes to match to identify the device;
        values are expected to be strings.

        Example:

        let's start with
        dom.metadata() ->
        <vm>
          <device id="dev0">
            <foo>bar</foo>
          </device>
          <device id="dev1">
            <number type="int">42</number>
          </device>
        </vm>

        let's run this code
        md_desc = Descriptor('vm')
        md_desc.load(dom)
        with md_desc.device(id='dev0') as vm:
           print(vm)

        will emit

        {
          'foo': 'bar'
        }
        """
        dev_data = self._find_device(kwargs)
        if dev_data is None:
            dev_data = self._add_device(kwargs)
        self._log.debug('device metadata: %s', dev_data)
        data = utils.picklecopy(dev_data)
        yield data
        dev_data.clear()
        dev_data.update(utils.picklecopy(data))
        self._log.debug('device metadata updated: %s', dev_data)

    @contextmanager
    def values(self):
        """
        Helper context manager to get and update the metadata of the vm.
        Any change performed to the device metadata is not committed
        to the underlying libvirt.Domain until dump() is called.

        :rtype: Python dict, whose keys are always strings.
                No nested objects are allowed.
        """
        with self._lock:
            data = self._values.copy()
        self._log.debug('values: %s', data)
        yield data
        with self._lock:
            self._values.clear()
            self._values.update(data)
        self._log.debug('values updated: %s', data)

    @property
    def custom(self):
        """
        Return the custom properties, as dict.
        The custom properties are sent by Engine and read-only.

        :rtype: Python dict, whose keys are always strings.
                No nested objects are allowed.
        """
        return self._custom.copy()

    def add_custom(self, values):
        """
        Add the custom properties.
        Usually Vdsm never needs to set custom variables, only
        to write them. The only exception is when Vdsm >= 4.2
        is deployed on a 4.1 cluster. We need to store what
        Engine sent as parameters.

        :param values: values to add to custom properties.
        :type values: dict, whose keys and values are strings.
                      No nesting allowed.
        """
        self._custom.update(values)

    def all_devices(self, **kwargs):
        """
        Return all the devices which match the given attributes.

        kwargs: each argument corresponds to a <device> attribute
        name and the value (string) to the attribute value;
        only devices having all the given values are returned.

        :rtype: dict

        Example:

        let's start with
        dom.metadata() ->
        <vm>
          <device kind="blah" id="dev0">
            <foo>bar</foo>
          </device>
          <device kind="blah" id="dev1">
            <number type="int">42</number>
          </device>
        </vm>

        let's run this code
        md_desc = Descriptor('vm')
        md_desc.load(dom)
        print([dev for dev in md_desc.all_devices(kind="blah")])

        will emit

        [{'foo': 'bar'}, {'number': 42}]
        """
        for data in self._matching_devices(kwargs):
            # A shallow copy ({}.copy) would have been enough.
            # We need to support complex storage devices, hence
            # we use picklecopy.
            yield utils.picklecopy(data)

    def _matching_devices(self, attrs_to_match):
        for (dev_attrs, dev_data) in self._devices:
            if _match_args(attrs_to_match, dev_attrs):
                yield dev_data

    def _parse_xml(self, xml_str):
        self._parse_tree(xmlutils.fromstring(xml_str))

    def _parse_tree(self, root):
        selector = '{%s}%s' % (self._namespace_uri, self._name)
        if root.tag == 'metadata':
            md_elem = root.find('./' + selector)
        else:
            md_elem = root.find('./metadata/' + selector)
        if md_elem is not None:
            md_uuid = root.find('./uuid')
            # UUID may not be present in hotplug/hotunplug metadata snippets
            uuid_text = '?' if md_uuid is None else md_uuid.text
            self._log.debug(
                'parsing metadata for %s: %s',
                uuid_text, xmlutils.tostring(md_elem, pretty=True))
            self._load(md_elem, self._namespace, self._namespace_uri)

    def _load(self, md_elem, namespace=None, namespace_uri=None):
        metadata_obj = Metadata(namespace, namespace_uri)
        md_data = metadata_obj.load(md_elem)
        custom_elem = metadata_obj.find(md_elem, _CUSTOM)
        with self._lock:
            if custom_elem is not None:
                self._custom = metadata_obj.load(custom_elem)
            else:
                self._custom = {}
            self._devices = [
                (dev.attrib.copy(), _load_device(metadata_obj, dev))
                for dev in metadata_obj.findall(md_elem, _DEVICE)
            ]
            md_data.pop(_CUSTOM, None)
            md_data.pop(_DEVICE, None)
            self._values = md_data

    def _build_tree(self, namespace=None, namespace_uri=None):
        metadata_obj = Metadata(namespace, namespace_uri)
        md_elem = metadata_obj.dump(self._name, **self._values)
        for (attrs, data) in self._devices:
            if data:
                dev_elem = _dump_device(metadata_obj, data)
                dev_elem.attrib.update(attrs)
                vmxml.append_child(md_elem, etree_child=dev_elem)
        if self._custom:
            custom_elem = metadata_obj.dump(_CUSTOM, **self._custom)
            vmxml.append_child(md_elem, etree_child=custom_elem)
        return md_elem

    def _build_xml(self, namespace=None, namespace_uri=None):
        with self._lock:
            md_elem = self._build_tree(namespace, namespace_uri)
            return xmlutils.tostring(md_elem, pretty=True)

    def _find_device(self, kwargs):
        devices = list(self._matching_devices(kwargs))
        if len(devices) > 1:
            raise MissingDevice()
        if not devices:
            return None
        return devices[0]

    def _add_device(self, attrs):
        data = {}
        self._devices.append((attrs.copy(), data))
        # yes, we want to return a mutable reference.
        return data


def _load_device(md_obj, dev):
    info = md_obj.load(dev)

    for key in _IGNORED_KEYS:
        info.pop(key, None)

    for key in _DEVICE_SUBKEYS:
        elem = md_obj.find(dev, key)
        if elem is not None:
            if key == _PORT_MIRRORING:
                value = _load_port_mirroring(md_obj, elem)
            elif key == _REPLICA:
                value = _load_device(md_obj, elem)
            elif key in _LAYERED_KEYS:
                value = _load_layered(md_obj, elem)
            elif key == _SPEC_PARAMS:
                value = _load_device_spec_params(md_obj, elem)
            elif key == _PAYLOAD:
                value = _load_payload(md_obj, elem)
            elif key == _CHANGE:
                value = _load_device(md_obj, elem)
            else:
                value = md_obj.load(elem)
            info[key] = value
    return info


def _load_layered(md_obj, elem):
    return [md_obj.load(node) for node in elem]


def _dump_layered(md_obj, key, subkey, value):
    chain = md_obj.make_element(key)
    for val in value:
        vmxml.append_child(
            chain,
            etree_child=md_obj.dump(subkey, **val)
        )
    return chain


def _dump_device(md_obj, data, node_name=_DEVICE):
    elems = []
    data = utils.picklecopy(data)

    for key in _IGNORED_KEYS:
        data.pop(key, None)

    for key in _DEVICE_SUBKEYS:
        value = data.pop(key, {})
        if not value and key in _NONEMPTY_KEYS:
            # empty elements make no sense
            continue

        if key == _PORT_MIRRORING:
            elems.append(_dump_port_mirroring(md_obj, value))
        elif key == _REPLICA:
            elems.append(_dump_device(md_obj, value, _REPLICA))
        elif key in _LAYERED_KEYS:
            elems.append(
                _dump_layered(md_obj, key, _LAYERED_KEYS[key], value)
            )
        elif key == _SPEC_PARAMS:
            elems.append(_dump_device_spec_params(md_obj, value))
        elif key == _PAYLOAD:
            elems.append(_dump_payload(md_obj, _PAYLOAD, value))
        elif key == _CHANGE:
            elems.append(_dump_device(md_obj, value, _CHANGE))
        else:
            elems.append(md_obj.dump(key, **value))

    dev_elem = md_obj.dump(node_name, **data)
    for elem in elems:
        vmxml.append_child(dev_elem, etree_child=elem)
    return dev_elem


def _load_port_mirroring(md_obj, elem):
    return [net.text for net in md_obj.findall(elem, _NETWORK)]


def _dump_port_mirroring(md_obj, value):
    return md_obj.dump_sequence(_PORT_MIRRORING, _NETWORK, value)


_VM_PAYLOAD = 'vmPayload'
_FILE_SPEC = 'file'
_PATH_SPEC = 'path'


def _load_payload(md_obj, payload_elem):
    payload = md_obj.load(payload_elem)
    payload[_FILE_SPEC] = {
        entry.attrib[_PATH_SPEC]: entry.text
        for entry in payload_elem
        if md_obj.match(entry, _FILE_SPEC)
    }
    return payload


def _load_device_spec_params(md_obj, elem):
    spec_params = md_obj.load(elem)
    payload_elem = md_obj.find(elem, _VM_PAYLOAD)
    if payload_elem is not None:
        spec_params[_VM_PAYLOAD] = _load_payload(md_obj, payload_elem)
    # ignore the IO tune settings if present (they should not), and
    # never deserialize it: we should read them from the libvirt
    # domain XML
    spec_params.pop(_IO_TUNE, None)
    return spec_params


def _dump_payload(md_obj, tag, value):
    file_spec = value.pop(_FILE_SPEC)
    payload_elem = md_obj.dump(tag, **value)

    for path, content in sorted(
        file_spec.items(),
        key=operator.itemgetter(0),
    ):
        entry = md_obj.make_element(_FILE_SPEC, parent=payload_elem)
        entry.attrib[_PATH_SPEC] = path
        entry.text = content

    return payload_elem


def _dump_device_spec_params(md_obj, value):
    # ignore if present, never serialize it: we should read the
    # IO tune settings from the libvirt domain XML
    value.pop(_IO_TUNE, None)
    payload = value.pop(_VM_PAYLOAD, None)
    if payload is not None:
        # mandatory for vmPayload
        payload_elem = _dump_payload(md_obj, _VM_PAYLOAD, payload)
    else:
        payload_elem = None

    spec_params_elem = md_obj.dump(_SPEC_PARAMS, **value)
    if payload_elem is not None:
        vmxml.append_child(spec_params_elem, etree_child=payload_elem)

    return spec_params_elem


def _match_args(kwargs, attrs):
    for key, value in kwargs.items():
        if key not in attrs or attrs[key] != value:
            return False
    return True


def _elem_to_keyvalue(elem):
    key = elem.tag
    value = elem.text
    data_type = elem.attrib.get('type')
    if data_type is None:
        # no data_type -> fallback to string
        if value is None:
            value = ''
    else:
        if value is None:
            if data_type == 'str':
                value = ''
            else:
                raise ValueError(
                    'unknown type hint for %r (%s): %r' % (
                        key, elem.attrib, value))
        if data_type == 'bool':
            value = conv.tobool(value)
        elif data_type == 'int':
            value = int(value)
        elif data_type == 'float':
            value = float(value)
    return key, value


def _keyvalue_to_elem(key, value, elem):
    subelem = ET.SubElement(elem, key)
    if isinstance(value, bool):
        subelem.attrib['type'] = 'bool'
    elif isinstance(value, int):
        subelem.attrib['type'] = 'int'
    elif isinstance(value, float):
        subelem.attrib['type'] = 'float'
    elif isinstance(value, six.string_types):
        pass
    else:
        raise UnsupportedType(key, value)
    subelem.text = str(value)
    return subelem
