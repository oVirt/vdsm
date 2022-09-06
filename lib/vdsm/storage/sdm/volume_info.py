# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

from contextlib import contextmanager

from vdsm.common import properties
from vdsm.storage import constants as sc
from vdsm.storage import resourceManager as rm
from vdsm.storage import volume
from vdsm.storage.sdc import sdCache


class VolumeInfo(properties.Owner):
    """
    VolumeInfo should be used for performing operations on any volume in a
    chain except shared volume.
    A volume is prepared in read-write mode.
    While performing operations, the volume is not set as illegal.
    """
    sd_id = properties.UUID(required=True)
    img_id = properties.UUID(required=True)
    vol_id = properties.UUID(required=True)
    generation = properties.Integer(required=False, minval=0,
                                    maxval=sc.MAX_GENERATION)

    def __init__(self, params, host_id):
        self.sd_id = params.get('sd_id')
        self.img_id = params.get('img_id')
        self.vol_id = params.get('vol_id')
        self.generation = params.get('generation')
        self._host_id = host_id
        self._vol = None

    @property
    def locks(self):
        img_ns = rm.getNamespace(sc.IMAGE_NAMESPACE, self.sd_id)
        ret = [rm.Lock(sc.STORAGE, self.sd_id, rm.SHARED),
               rm.Lock(img_ns, self.img_id, rm.EXCLUSIVE)]
        dom = sdCache.produce_manifest(self.sd_id)
        if dom.hasVolumeLeases():
            ret.append(volume.VolumeLease(self._host_id, self.sd_id,
                                          self.img_id, self.vol_id))
        return ret

    @property
    def path(self):
        return self.volume.getVolumePath()

    @property
    def volume(self):
        if self._vol is None:
            dom = sdCache.produce_manifest(self.sd_id)
            self._vol = dom.produceVolume(self.img_id, self.vol_id)
        return self._vol

    def volume_operation(self):
        return self.volume.operation(requested_gen=self.generation,
                                     set_illegal=False)

    @contextmanager
    def prepare(self):
        self.volume.prepare(rw=True, justme=False)
        try:
            yield
        finally:
            self.volume.teardown(self.sd_id, self.vol_id, justme=False)
