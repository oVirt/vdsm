# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

from vdsm.common import properties
from vdsm.storage import exception as se
from vdsm.storage.constants import STORAGE
from vdsm.storage import resourceManager as rm
from vdsm.storage.sdc import sdCache

from . import base


class Job(base.Job):
    """
    Moves the data from given device to other devices of the domain.
    """

    def __init__(self, job_id, host_id, move_params):
        super(Job, self).__init__(job_id, 'move_device', host_id)
        self.params = StorageDomainDeviceMoveParams(move_params)

    def _run(self):
        sd_manifest = sdCache.produce_manifest(self.params.sd_id)
        if not sd_manifest.supports_device_reduce():
            raise se.UnsupportedOperation(
                "Storage domain does not support moving devices",
                sdUUID=sd_manifest.sdUUID(),
                sdType=sd_manifest.getStorageType())
        # TODO: we assume at this point that the domain isn't active and can't
        # be activated - we need to ensure that.
        with rm.acquireResource(STORAGE, self.params.sd_id, rm.EXCLUSIVE):
            with sd_manifest.domain_id(self.host_id), \
                    sd_manifest.domain_lock(self.host_id):
                sd_manifest.movePV(self.params.src_guid, self.params.dst_guids)


class StorageDomainDeviceMoveParams(properties.Owner):
    sd_id = properties.UUID(required=True)
    src_guid = properties.String(required=True)

    def __init__(self, params):
        self.sd_id = params.get('sd_id')
        self.src_guid = params.get('src_guid')
        dst_guids = params.get('dst_guids') or []
        # TODO: using properties.List for dst_guids when it is available
        self.dst_guids = frozenset(dst_guids)

        if type(dst_guids) is not list:
            raise ValueError("dst_guids is not a list")

        for item in dst_guids:
            if not isinstance(item, str):
                raise ValueError("dst_guids item %s isn't a string" % item)

        if len(self.dst_guids) != len(dst_guids):
            raise ValueError("dst_guids contains duplicate values")

        if self.src_guid in self.dst_guids:
            raise ValueError("src_guid is in dst_guids")
