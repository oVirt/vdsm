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

    def __init__(self, xml_tree, uuid, log, image=None):
        self._xml_tree = xml_tree
        self._uuid = uuid
        self._log = log
        self._image = image

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

    def drives(self):
        images, volumes = [], []
        disks = self._xml_tree.findall('.//disk[@type="file"]')
        for disk in disks:
            # TODO: add in the findall() above?
            device = disk.get('device')
            if device == 'cdrom':
                target = images
            elif device == 'disk':
                target = volumes
            else:
                continue
            source = disk.find('./source/[@file]')
            if source is None:
                continue
            image_path = source.get('file')
            self._log.debug('runtime %r found image path %r',
                            self.uuid, image_path)
            target.append(image_path.strip('"'))
        image = self._find_image(images)
        return image, volumes

    def drives_map(self):
        mapping = {}
        entries = self._xml_tree.findall(
            './metadata/{%s}drivemap/volume' % (
                xmlconstants.METADATA_VM_DRIVE_MAP_URI
            ),
        )
        for entry in entries:
            name = entry.get('name')
            drive = entry.get('drive')
            mapping[name] = drive
        return mapping

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
        if self._image is None:
            return images[0]
        return self._image
