#
# Copyright 2016 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 2 of the License, or
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
Utilities to process XML file (aka libvirt domain definitions).
"""

from __future__ import absolute_import

import logging
import os.path
import xml.etree.ElementTree as ET

import six

from vdsm.virt import metadata
from vdsm.virt import xmlconstants
from vdsm.utils import rmFile
from vdsm import constants


STATE_DIR = os.path.join(constants.P_VDSM_RUN, 'containers')


class XMLFile(object):

    _log = logging.getLogger('virt.containers.XMLFile')

    @staticmethod
    def encode(root):
        encoding = 'utf-8' if six.PY2 else 'unicode'
        return ET.tostring(root, encoding=encoding)

    def __init__(self, name):
        self._name = name

    @property
    def path(self):
        return os.path.join(
            STATE_DIR,
            '%s.xml' % (self._name)
        )

    def load(self):
        return ET.fromstring(self.read())

    def read(self):
        self._log.debug('loading XML %r', self._name)
        with open(self.path, 'rt') as src:
            return src.read()

    def save(self, root):
        self._log.debug('saving XML %r', self._name)
        with open(self.path, 'wt') as dst:
            dst.write(XMLFile.encode(root))

    def clear(self):
        self._log.debug('clearing XML for container %s', self._name)
        rmFile(self.path)


class ConfigError(Exception):
    """
    TODO
    """


class DomainParser(object):

    def __init__(self, xml_tree, uuid, log):
        self._xml_tree = xml_tree
        self._uuid = uuid
        self._log = log

    @property
    def uuid(self):
        return self._uuid

    def memory(self):
        mem_node = self._xml_tree.find('./maxMemory')
        if mem_node is not None:
            mem = int(mem_node.text) / 1024
            self._log.debug('runtime %r found memory = %i MiB',
                            self.uuid, mem)
            return mem
        raise ConfigError('memory')

    def volumes(self):
        vols = []
        disks = self._xml_tree.findall('.//disk[@type="file"]')
        for disk in disks:
            # TODO: add in the findall() above?
            device = disk.get('device')
            if device != 'disk':
                continue
            source = disk.find('./source/[@file]')
            if source is None:
                continue
            image_path = source.get('file')
            self._log.debug('runtime %r found image path %r',
                            self.uuid, image_path)
            vols.append(image_path.strip('"'))
        return vols

    def image(self):
        cont_elem = self._xml_tree.find(
            './metadata/{%s}container' % (
                xmlconstants.METADATA_CONTAINERS_URI
            )
        )
        if cont_elem is None:
            raise ConfigError('missing container configuration')
        md = metadata.Metadata(
            xmlconstants.METADATA_CONTAINERS_PREFIX,
            xmlconstants.METADATA_CONTAINERS_URI
        )
        img = md.load(cont_elem).get('image')
        if img is None:
            raise ConfigError('missing image to run')
        return img

    def drives_map(self):
        mapping_elem = self._xml_tree.find(
            './metadata/{%s}drivemap' % (
                xmlconstants.METADATA_VM_DRIVE_MAP_URI
            )
        )
        if not mapping_elem:
            return {}
        md = metadata.Metadata(
            xmlconstants.METADATA_VM_DRIVE_MAP_PREFIX,
            xmlconstants.METADATA_VM_DRIVE_MAP_URI
        )
        return md.load(mapping_elem).copy()

    def network(self):
        interfaces = self._xml_tree.findall('.//interface[@type="bridge"]')
        for interface in interfaces:
            link = interface.find('./link')
            if link.get('state') != 'up':
                continue
            source = interface.find('./source[@bridge]')
            if source is None:
                continue
            bridge = source.get('bridge')
            self._log.debug('runtime %r found bridge %r', self.uuid, bridge)
            return bridge.strip('"')
        raise ConfigError('network settings not found')  # TODO

    def _find_image(self, images):
        if not images:
            raise ConfigError('image path not found')
        if len(images) > 1:
            self._log.warning(
                'found more than one image: %r, using the first one',
                images)
        return images[0]
