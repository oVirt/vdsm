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
    Reduces the device from the given domain devices.
    """

    def __init__(self, job_id, host_id, reduce_params):
        super(Job, self).__init__(job_id, 'reduce_domain', host_id)
        self.params = StorageDomainReduceParams(reduce_params)

    def _run(self):
        sd_manifest = sdCache.produce_manifest(self.params.sd_id)
        if not sd_manifest.supports_device_reduce():
            raise se.UnsupportedOperation(
                "Storage domain does not support reduce operation",
                sdUUID=sd_manifest.sdUUID(),
                sdType=sd_manifest.getStorageType())
        # TODO: we assume at this point that the domain isn't active and can't
        # be activated - we need to ensure that.
        with rm.acquireResource(STORAGE, self.params.sd_id, rm.EXCLUSIVE):
            with sd_manifest.domain_id(self.host_id), \
                    sd_manifest.domain_lock(self.host_id):
                sd_manifest.reduceVG(self.params.guid)


class StorageDomainReduceParams(properties.Owner):
    sd_id = properties.UUID(required=True)
    guid = properties.String(required=True)

    def __init__(self, params):
        self.sd_id = params.get('sd_id')
        self.guid = params.get('guid')
