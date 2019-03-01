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


class VolumeMetadata(object):

    log = logging.getLogger('storage.VolumeMetadata')

    def __init__(self, domain, image, puuid, capacity, format, type, voltype,
                 disktype, description="", legality=sc.ILLEGAL_VOL, ctime=None,
                 generation=sc.DEFAULT_GENERATION):
        # Storage domain UUID
        self.domain = domain
        # Image UUID
        self.image = image
        # UUID of the parent volume or BLANK_UUID
        self.puuid = puuid
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

    @classmethod
    def from_lines(cls, lines):
        md = {}
        for line in lines:
            if line.startswith("EOF"):
                break
            if '=' not in line:
                continue
            key, value = line.split('=', 1)
            md[key.strip()] = value.strip()

        try:
            # We work internally in bytes, even if old format store
            # value in blocks, we will read SIZE instead of CAPACITY
            # from non-converted volumes and use it
            if sc.CAPACITY in md:
                capacity = int(md[sc.CAPACITY])
            else:
                capacity = int(md[sc.SIZE]) * sc.BLOCK_SIZE_512

            return cls(domain=md[sc.DOMAIN],
                       image=md[sc.IMAGE],
                       puuid=md[sc.PUUID],
                       capacity=capacity,
                       format=md[sc.FORMAT],
                       type=md[sc.TYPE],
                       voltype=md[sc.VOLTYPE],
                       disktype=md[sc.DISKTYPE],
                       description=md[sc.DESCRIPTION],
                       legality=md[sc.LEGALITY],
                       ctime=int(md[sc.CTIME]),
                       # generation was added to the set of metadata keys well
                       # after the above fields.  Therefore, it may not exist
                       # on storage for pre-existing volumes.  In that case we
                       # report a default value of 0 which will be written to
                       # the volume metadata on the next metadata change.
                       generation=int(md.get(sc.GENERATION,
                                             sc.DEFAULT_GENERATION)))
        except KeyError as e:
            if "NONE" in md:
                # Before 4.20.34-1 (ovirt 4.2.5) volume metadata could be
                # cleared by writing invalid metadata when deleting a volume.
                # See https://bugzilla.redhat.com/1574631.
                raise exception.MetadataCleared("lines={}".format(lines))

            raise exception.MetaDataKeyNotFoundError(
                "key={} lines={}".format(e, lines))

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
    def size(self):
        return self.capacity // sc.BLOCK_SIZE_512

    @size.setter
    def size(self, value):
        self.capacity = (self._validate_integer("size", value) *
                         sc.BLOCK_SIZE_512)

    @property
    def generation(self):
        return self._generation

    @generation.setter
    def generation(self, value):
        self._generation = self._validate_integer("generation", value)

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
        Format metadata string in storage format.

        VolumeMetadata is quite restrictive and doesn't allows
        you to make an invalid metadata, but sometimes, for example
        for a format conversion, you need some additional fields to
        be written to the storage. Those fields can be added using
        overrides dict.

        Raises MetadataOverflowError if formatted metadata is too long.

        NOTE: Not used yet! We need to drop legacy_info() and pass
        VolumeMetadata instance instead of a dict to use this code.
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
            sc.PUUID: self.puuid,
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
            info[sc.SIZE] = self.size
        else:
            info[sc.CAPACITY] = self.capacity

        info.update(overrides)

        keys = sorted(info.keys())
        lines = ["%s=%s\n" % (key, info[key]) for key in keys]
        lines.append("EOF\n")
        data = "".join(lines)
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
        sc.PUUID: 'puuid',
        sc.LEGALITY: 'legality',
        sc.GENERATION: 'generation',
        sc.SIZE: 'size'
    }

    def __getitem__(self, item):
        try:
            value = getattr(self, self._fieldmap[item])
        except AttributeError:
            raise KeyError(item)

        # Some fields needs to be converted to string
        if item in (sc.CAPACITY, sc.SIZE, sc.CTIME):
            value = str(value)
        return value

    def __setitem__(self, item, value):
        setattr(self, self._fieldmap[item], value)

    def get(self, item, default=None):
        try:
            return self[item]
        except KeyError:
            return default
