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

    def __init__(self, domain, image, puuid, size, format,
                 type, voltype, disktype, description="",
                 legality=sc.ILLEGAL_VOL, ctime=None, mtime=None,
                 generation=sc.DEFAULT_GENERATION):
        if not isinstance(size, six.integer_types):
            raise AssertionError("Invalid value for 'size': {!r}".format(size))
        if ctime is not None and not isinstance(ctime, int):
            raise AssertionError(
                "Invalid value for 'ctime': {!r}".format(ctime))
        if mtime is not None and not isinstance(mtime, int):
            raise AssertionError(
                "Invalid value for 'mtime': {!r}".format(mtime))
        if not isinstance(generation, int):
            raise AssertionError(
                "Invalid value for 'generation': {!r}".format(generation))

        # Storage domain UUID
        self.domain = domain
        # Image UUID
        self.image = image
        # UUID of the parent volume or BLANK_UUID
        self.puuid = puuid
        # Volume size in blocks
        self.size = size
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
        # Volume modification time (unused and should be zero)
        self.mtime = 0 if mtime is None else mtime
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
            return cls(domain=md[sc.DOMAIN],
                       image=md[sc.IMAGE],
                       puuid=md[sc.PUUID],
                       size=int(md[sc.SIZE]),
                       format=md[sc.FORMAT],
                       type=md[sc.TYPE],
                       voltype=md[sc.VOLTYPE],
                       disktype=md[sc.DISKTYPE],
                       description=md[sc.DESCRIPTION],
                       legality=md[sc.LEGALITY],
                       ctime=int(md[sc.CTIME]),
                       mtime=int(md[sc.MTIME]),
                       # generation was added to the set of metadata keys well
                       # after the above fields.  Therefore, it may not exist
                       # on storage for pre-existing volumes.  In that case we
                       # report a default value of 0 which will be written to
                       # the volume metadata on the next metadata change.
                       generation=int(md.get(sc.GENERATION,
                                             sc.DEFAULT_GENERATION)))
        except KeyError as e:
            raise exception.MetaDataKeyNotFoundError(
                "Missing metadata key: %s: found: %s" % (e, md))

    @property
    def description(self):
        return self._description

    @description.setter
    def description(self, desc):
        self._description = self.validate_description(desc)

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

    def storage_format(self):
        """
        Format metadata string in storage format.

        Raises MetadataOverflowError if formatted metadata is too long.
        """
        info = self.legacy_info()
        keys = sorted(info.keys())
        lines = ["%s=%s\n" % (key, info[key]) for key in keys]
        lines.append("EOF\n")
        data = "".join(lines)
        if len(data) > sc.METADATA_SIZE:
            raise exception.MetadataOverflowError(data)
        return data

    def legacy_info(self):
        """
        Return metadata in dictionary format
        """
        return {
            sc.FORMAT: self.format,
            sc.TYPE: self.type,
            sc.VOLTYPE: self.voltype,
            sc.DISKTYPE: self.disktype,
            sc.SIZE: str(self.size),
            sc.CTIME: str(self.ctime),
            sc.POOL: "",  # obsolete
            sc.DOMAIN: self.domain,
            sc.IMAGE: self.image,
            sc.DESCRIPTION: self.description,
            sc.PUUID: self.puuid,
            sc.MTIME: str(self.mtime),
            sc.LEGALITY: self.legality,
            sc.GENERATION: self.generation,
        }
