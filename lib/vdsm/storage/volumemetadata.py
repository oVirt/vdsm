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

import logging
import time

import six

from vdsm.storage import constants as sc
from vdsm.storage import exception


# SIZE property was deprecated in metadata v5, but we still need this key to
# read and write legacy metadata. To make sure no other code use it and it's
# used only by metadata code, move it here and make it private.
_SIZE = "SIZE"

ATTRIBUTES = {
    sc.DOMAIN: ("domain", str),
    sc.IMAGE: ("image", str),
    sc.PUUID: ("parent", str),
    sc.CAPACITY: ("capacity", int),
    sc.FORMAT: ("format", str),
    sc.TYPE: ("type", str),
    sc.VOLTYPE: ("voltype", str),
    sc.DISKTYPE: ("disktype", str),
    sc.DESCRIPTION: ("description", str),
    sc.LEGALITY: ("legality", str),
    sc.CTIME: ("ctime", int),
    sc.GENERATION: ("generation", int),
    sc.SEQUENCE: ("sequence", int),
}


def _lines_to_dict(lines):
    md = {}
    errors = []

    for line in lines:
        # Skip a line if there is invalid value.
        try:
            line = line.decode("utf-8")
        except UnicodeDecodeError as e:
            errors.append("Invalid line '{}': {}".format(line, e))
            continue

        if line.startswith("EOF"):
            break
        if '=' not in line:
            continue

        key, value = line.split('=', 1)
        md[key.strip()] = value.strip()

    return md, errors


def parse(lines):
    md, errors = _lines_to_dict(lines)
    metadata = {}

    if "NONE" in md:
        # Before 4.20.34-1 (ovirt 4.2.5) volume metadata could be
        # cleared by writing invalid metadata when deleting a volume.
        # See https://bugzilla.redhat.com/1574631.
        errors.append(str(exception.MetadataCleared()))
        return {}, errors

    # We work internally in bytes, even if old format store
    # value in blocks, we will read SIZE instead of CAPACITY
    # from non-converted volumes and use it
    if _SIZE in md and sc.CAPACITY not in md:
        try:
            md[sc.CAPACITY] = int(md[_SIZE]) * sc.BLOCK_SIZE_512
        except ValueError as e:
            errors.append(str(e))

    if sc.GENERATION not in md:
        md[sc.GENERATION] = sc.DEFAULT_GENERATION

    if sc.SEQUENCE not in md:
        md[sc.SEQUENCE] = sc.DEFAULT_SEQUENCE

    for key, (name, validate) in ATTRIBUTES.items():
        try:
            # FIXME: remove pylint skip when bug fixed:
            # https://github.com/PyCQA/pylint/issues/5113
            metadata[name] = validate(md[key])  # pylint: disable=not-callable
        except KeyError:
            errors.append("Required key '{}' is missing.".format(name))
        except ValueError as e:
            errors.append("Invalid '{}' value: {}".format(name, str(e)))

    return metadata, errors


def dump(lines):
    md, errors = parse(lines)
    if errors:
        logging.warning(
            "Invalid metadata found errors=%s", errors)
        md["status"] = sc.VOL_STATUS_INVALID
    else:
        md["status"] = sc.VOL_STATUS_OK

    # Do not include domain in dump output.
    md.pop("domain", None)

    return md


class VolumeMetadata(object):

    log = logging.getLogger('storage.volumemetadata')

    def __init__(self, domain, image, parent, capacity, format, type, voltype,
                 disktype, description="", legality=sc.ILLEGAL_VOL, ctime=None,
                 generation=sc.DEFAULT_GENERATION,
                 sequence=sc.DEFAULT_SEQUENCE):
        # Storage domain UUID
        self.domain = domain
        # Image UUID
        self.image = image
        # UUID of the parent volume or BLANK_UUID
        self.parent = parent
        # Volume capacity in bytes
        self.capacity = capacity
        # Format (RAW or COW)
        self.format = format
        # Allocation policy (PREALLOCATED or SPARSE)
        self.type = type
        # Relationship to other volumes (LEAF, INTERNAL or SHARED)
        self.voltype = voltype
        # Intended usage of this volume (unused)
        self.disktype = disktype
        # Free-form description and may be used to store extra metadata
        self.description = description
        # Indicates if the volume contents should be considered valid
        self.legality = legality
        # Volume creation time (in seconds since the epoch)
        self.ctime = int(time.time()) if ctime is None else ctime
        # Generation increments each time certain operations complete
        self.generation = generation
        # Sequence number of the volume, increased every time a new volume is
        # created in an image.
        self.sequence = sequence

    @classmethod
    def from_lines(cls, lines):
        '''
        Instantiates a VolumeMetadata object from storage read bytes.

        Args:
            lines: list of key=value entries given as bytes read from storage
            metadata section. "EOF" entry terminates parsing.
        '''

        metadata, errors = parse(lines)
        if errors:
            raise exception.InvalidMetadata(
                "lines={} errors={}".format(lines, errors))
        return cls(**metadata)

    @property
    def description(self):
        return self._description

    @description.setter
    def description(self, desc):
        self._description = self.validate_description(desc)

    @property
    def capacity(self):
        return self._capacity

    @capacity.setter
    def capacity(self, value):
        self._capacity = self._validate_integer("capacity", value)

    @property
    def ctime(self):
        return self._ctime

    @ctime.setter
    def ctime(self, value):
        self._ctime = self._validate_integer("ctime", value)

    @property
    def generation(self):
        return self._generation

    @generation.setter
    def generation(self, value):
        self._generation = self._validate_integer("generation", value)

    @property
    def sequence(self):
        return self._sequence

    @sequence.setter
    def sequence(self, value):
        self._sequence = self._validate_integer("sequence", value)

    @classmethod
    def _validate_integer(cls, property, value):
        if not isinstance(value, six.integer_types):
            raise AssertionError(
                "Invalid value for metadata property {!r}: {!r}".format(
                    property, value))
        return value

    @classmethod
    def validate_description(cls, desc):
        desc = str(desc)
        # We cannot fail when the description is too long, since we must
        # support older engine that may send such values, or old disks
        # with long description.
        if len(desc) > sc.DESCRIPTION_SIZE:
            cls.log.warning("Description is too long, truncating to %d bytes",
                            sc.DESCRIPTION_SIZE)
            desc = desc[:sc.DESCRIPTION_SIZE]
        return desc

    def storage_format(self, domain_version, **overrides):
        """
        Format metadata parameters into storage format bytes.

        VolumeMetadata is quite restrictive and does not allow
        you to make an invalid metadata, but sometimes, for example
        for a format conversion, you need some additional fields to
        be written to the storage. Those fields can be added using
        overrides dict.

        Raises MetadataOverflowError if formatted metadata is too long.
        """

        info = {
            sc.CTIME: str(self.ctime),
            sc.DESCRIPTION: self.description,
            sc.DISKTYPE: self.disktype,
            sc.DOMAIN: self.domain,
            sc.FORMAT: self.format,
            sc.GENERATION: self.generation,
            sc.IMAGE: self.image,
            sc.LEGALITY: self.legality,
            sc.PUUID: self.parent,
            sc.TYPE: self.type,
            sc.VOLTYPE: self.voltype,
        }
        if domain_version < 5:
            # Always zero on pre v5 domains
            # We need to keep MTIME available on pre v5
            # domains, as other code is expecting that
            # field to exists and will fail without it.
            info[sc.MTIME] = 0

            # Pre v5 domains should have SIZE in blocks
            # instead of CAPACITY in bytes
            info[_SIZE] = self.capacity // sc.BLOCK_SIZE_512
        else:
            info[sc.CAPACITY] = self.capacity
            info[sc.SEQUENCE] = self.sequence

        info.update(overrides)

        keys = sorted(info.keys())
        lines = ["%s=%s\n" % (key, info[key]) for key in keys]
        lines.append("EOF\n")
        data = "".join(lines).encode("utf-8")
        if len(data) > sc.METADATA_SIZE:
            raise exception.MetadataOverflowError(data)
        return data

    # Three defs below allow us to imitate a dictionary
    # So intstead of providing a method to return a dictionary
    # with values, we return self and mimick dict behaviour.
    # In the fieldmap we keep mapping between metadata
    # field name and our internal field names
    #
    # TODO: All dict specific code below should be removed, when rest of VDSM
    # will be refactored, to use VolumeMetadata properties, instead of dict

    _fieldmap = {
        sc.FORMAT: 'format',
        sc.TYPE: 'type',
        sc.VOLTYPE: 'voltype',
        sc.DISKTYPE: 'disktype',
        sc.CAPACITY: 'capacity',
        sc.CTIME: 'ctime',
        sc.DOMAIN: 'domain',
        sc.IMAGE: 'image',
        sc.DESCRIPTION: 'description',
        sc.PUUID: 'parent',
        sc.LEGALITY: 'legality',
        sc.GENERATION: 'generation',
        sc.SEQUENCE: "sequence",
    }

    def __getitem__(self, item):
        try:
            value = getattr(self, self._fieldmap[item])
        except AttributeError:
            raise KeyError(item)

        # Some fields needs to be converted to string
        if item in (sc.CAPACITY, sc.CTIME):
            value = str(value)
        return value

    def __setitem__(self, item, value):
        setattr(self, self._fieldmap[item], value)

    def get(self, item, default=None):
        try:
            return self[item]
        except KeyError:
            return default

    def dump(self):
        return {
            "capacity": self.capacity,
            "ctime": self.ctime,
            "description": self.description,
            "disktype": self.disktype,
            "format": self.format,
            "generation": self.generation,
            "sequence": self.sequence,
            "image": self.image,
            "legality": self.legality,
            "parent": self.parent,
            "type": self.type,
            "voltype": self.voltype,
        }
