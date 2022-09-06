# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

"""
validators - Vdsm storage types and validators

This module includes the storage types mentioned in Vdsm schema.
The purpose of these types is to validate the input and provide an
easy way to pass the arguments around.
"""

from __future__ import absolute_import

from vdsm.common import properties
from vdsm.storage import constants as sc


class Lease(properties.Owner):
    """
    External sanlock lease.
    """
    sd_id = properties.UUID(required=True)
    lease_id = properties.UUID(required=True)

    def __init__(self, params):
        self.sd_id = params.get("sd_id")
        self.lease_id = params.get("lease_id")


class JobMetadata(properties.Owner):
    """
    JobMetadata - stored on external leases
    """
    type = properties.Enum(required=True, values=("JOB"))
    generation = properties.Integer(
        required=True, minval=0, maxval=sc.MAX_GENERATION)
    job_id = properties.UUID(required=True)
    job_status = properties.Enum(
        required=True,
        values=("PENDING", "FAILED", "SUCCEEDED", "FENCED"))

    def __init__(self, params):
        self.type = params.get("type")
        self.generation = params.get("generation")
        self.job_id = params.get("job_id")
        self.job_status = params.get("job_status")


class VolumeAttributes(properties.Owner):

    generation = properties.Integer(required=False, minval=0,
                                    maxval=sc.MAX_GENERATION)
    description = properties.String(required=False)

    def __init__(self, params):
        self.generation = params.get("generation")
        self.description = params.get("description")
        # TODO use properties.Enum when it supports optional enum
        self.type = params.get("type")
        # TODO use properties.Enum when it supports optional enum
        self.legality = params.get("legality")
        self._validate()

    def _validate(self):
        if self._is_empty():
            raise ValueError("No attributes to update")
        self._validate_type()
        self._validate_legality()

    def _is_empty(self):
        return (self.description is None and
                self.generation is None and
                self.legality is None and
                self.type is None)

    def _validate_type(self):
        if self.type is not None:
            if self.type != sc.type2name(sc.SHARED_VOL):
                raise ValueError("Volume type not supported %s"
                                 % self.type)

    def _validate_legality(self):
        if self.legality is not None:
            if self.legality not in [sc.LEGAL_VOL, sc.ILLEGAL_VOL]:
                raise ValueError("Legality not supported %s" % self.legality)

    def __repr__(self):
        values = ["%s=%r" % (key, value)
                  for key, value in vars(self).items()
                  if value is not None]
        return "<VolumeAttributes %s at 0x%x>" % (", ".join(values), id(self))
