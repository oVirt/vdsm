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

from vdsm.storage import constants
from vdsm.storage import exception


class VolumeMetadata(object):

    log = logging.getLogger('storage.VolumeMetadata')

    def __init__(self, domain, image, puuid, size, format,
                 type, voltype, disktype, description="",
                 legality=constants.ILLEGAL_VOL, ctime=None, mtime=None,
                 generation=constants.DEFAULT_GENERATION):
        if not isinstance(size, int):
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
            return cls(domain=md[constants.DOMAIN],
                       image=md[constants.IMAGE],
                       puuid=md[constants.PUUID],
                       size=int(md[constants.SIZE]),
                       format=md[constants.FORMAT],
                       type=md[constants.TYPE],
                       voltype=md[constants.VOLTYPE],
                       disktype=md[constants.DISKTYPE],
                       description=md[constants.DESCRIPTION],
                       legality=md[constants.LEGALITY],
                       ctime=int(md[constants.CTIME]),
                       mtime=int(md[constants.MTIME]),
                       # generation was added to the set of metadata keys well
                       # after the above fields.  Therefore, it may not exist
                       # on storage for pre-existing volumes.  In that case we
                       # report a default value of 0 which will be written to
                       # the volume metadata on the next metadata change.
                       generation=int(md.get(constants.GENERATION,
                                             constants.DEFAULT_GENERATION)))
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
        if len(desc) > constants.DESCRIPTION_SIZE:
            cls.log.warning("Description is too long, truncating to %d bytes",
                            constants.DESCRIPTION_SIZE)
            desc = desc[:constants.DESCRIPTION_SIZE]
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
        if len(data) > constants.METADATA_SIZE:
            raise exception.MetadataOverflowError(data)
        return data

    def legacy_info(self):
        """
        Return metadata in dictionary format
        """
        return {
            constants.FORMAT: self.format,
            constants.TYPE: self.type,
            constants.VOLTYPE: self.voltype,
            constants.DISKTYPE: self.disktype,
            constants.SIZE: str(self.size),
            constants.CTIME: str(self.ctime),
            constants.POOL: "",  # obsolete
            constants.DOMAIN: self.domain,
            constants.IMAGE: self.image,
            constants.DESCRIPTION: self.description,
            constants.PUUID: self.puuid,
            constants.MTIME: str(self.mtime),
            constants.LEGALITY: self.legality,
            constants.GENERATION: self.generation,
        }
