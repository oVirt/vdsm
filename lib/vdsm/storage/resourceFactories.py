# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import os

from vdsm.config import config
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import lvm
from vdsm.storage import resourceManager as rm
from vdsm.storage.sdc import sdCache

import logging

log = logging.getLogger('storage.resourcesfactories')


class LvmActivation(object):
    """
    Represents activation state of the LV.
    When the resource is created (i.e. the LV is being activated)
    it calls lvm.activateLVs(). When the resource is being finally released
    the close() calls lvm.deactivateLVs() to release the DM mappings
    for this volume.
    """
    def __init__(self, vg, lv, lockType):
        self._vg = vg
        self._lv = lv

        lvm.activateLVs(self._vg, [self._lv])

    def close(self):
        try:
            lvm.deactivateLVs(self._vg, [self._lv])
        except Exception as e:
            # If storage not accessible or lvm error occurred
            # the LV deactivation will failure.
            # We can live with it and still release the resource.
            log.warn("Failure deactivate LV %s/%s (%s)", self._vg, self._lv, e)


class LvmActivationFactory(rm.SimpleResourceFactory):
    def __init__(self, vg):
        rm.SimpleResourceFactory.__init__(self)
        self._vg = vg

    def resourceExists(self, resourceName):
        try:
            lvm.getLV(self._vg, resourceName)
            res = True
        except se.LogicalVolumeDoesNotExistError:
            res = False

        return res

    def createResource(self, resourceName, lockType):
        return LvmActivation(self._vg, resourceName, lockType)


class ImageResource(object):
    """
    Represents resource for image's volumes.
    """
    def __init__(self, volResourcesList):
        self.volResourcesList = volResourcesList

    def close(self):
        # Release template/volumes locks
        for volRes in self.volResourcesList:
            volRes.release()


class ImageResourceFactory(rm.SimpleResourceFactory):
    """
    This factory produce resources for images
    """
    # Resource timeouts are in seconds. It's written in ms in the config for
    # backward competability reasons
    resource_default_timeout = config.getint('irs',
                                             'prepare_image_timeout') / 1000.0

    def __init__(self, sdUUID):
        rm.SimpleResourceFactory.__init__(self)
        self.sdUUID = sdUUID
        self.volumeResourcesNamespace = rm.getNamespace(sc.VOLUME_NAMESPACE,
                                                        self.sdUUID)

    def __getResourceCandidatesList(self, resourceName, lockType):
        """
        Return list of lock candidates (template and volumes)
        """
        # Must be imported here due to import cycles.
        # TODO: Move getChain to another module to we can use normal import.
        import vdsm.storage.image as image

        volResourcesList = []
        template = None
        dom = sdCache.produce(sdUUID=self.sdUUID)
        # Get the list of the volumes
        repoPath = os.path.join(sc.REPO_DATA_CENTER, dom.getPools()[0])
        try:
            chain = image.Image(repoPath).getChain(sdUUID=self.sdUUID,
                                                   imgUUID=resourceName)
        except se.ImageDoesNotExistInSD:
            log.debug("Image %s does not exist in domain %s",
                      resourceName, self.sdUUID)
            return []

        # check if the chain is build above a template, or it is a standalone
        pvol = chain[0].getParentVolume()
        if pvol:
            template = pvol.volUUID
        elif chain[0].isShared():
            # Image of template itself,
            # with no other volumes in chain
            template = chain[0].volUUID
            del chain[:]

        volUUIDChain = [vol.volUUID for vol in chain]
        volUUIDChain.sort()

        # Activate all volumes in chain at once.
        # We will attempt to activate all volumes again down to the flow with
        # no consequence, since they are already active.
        # TODO Fix resource framework to hold images, instead of specific vols.
        # This assumes that chains can not spread into more than one SD.
        if dom.__class__.__name__ == "BlockStorageDomain":
            lvm.activateLVs(self.sdUUID, volUUIDChain)

        failed = False
        # Acquire template locks:
        # - 'lockType' for template's image itself
        # - Always 'shared' lock for image based on template
        try:
            if template:
                if len(volUUIDChain) > 0:
                    volRes = rm.acquireResource(
                        self.volumeResourcesNamespace,
                        template, rm.SHARED,
                        timeout=self.resource_default_timeout)
                else:
                    volRes = rm.acquireResource(
                        self.volumeResourcesNamespace,
                        template, lockType,
                        timeout=self.resource_default_timeout)
                volResourcesList.append(volRes)

            # Acquire 'lockType' volume locks
            for volUUID in volUUIDChain:
                volRes = rm.acquireResource(
                    self.volumeResourcesNamespace,
                    volUUID, lockType,
                    timeout=self.resource_default_timeout)

                volResourcesList.append(volRes)
        except (rm.RequestTimedOutError, se.ResourceAcqusitionFailed) as e:
            log.debug("Cannot acquire volume resource (%s)", str(e))
            failed = True
            raise
        except Exception:
            log.debug("Cannot acquire volume resource", exc_info=True)
            failed = True
            raise
        finally:
            if failed:
                # Release already acquired template/volumes locks
                for volRes in volResourcesList:
                    volRes.release()

        return volResourcesList

    def createResource(self, resourceName, lockType):
        volResourcesList = self.__getResourceCandidatesList(resourceName,
                                                            lockType)
        return ImageResource(volResourcesList)
