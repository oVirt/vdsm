#
# Copyright 2010-2011 Red Hat, Inc.
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


"""
Generic LVM interface wrapper

Incapsulates the actual LVM mechanics.
"""
import errno

import os
import re
import pwd
import grp
import logging
from collections import namedtuple
import pprint as pp
import threading
from itertools import chain
from subprocess import list2cmdline

from vdsm import constants
import misc
import multipath
import storage_exception as se
from vdsm.config import config
import devicemapper

log = logging.getLogger("Storage.LVM")

LVM_DEFAULT_TTL = 100

PV_FIELDS = ("uuid,name,size,vg_name,vg_uuid,pe_start,pe_count,"
             "pe_alloc_count,mda_count,dev_size")
VG_FIELDS = ("uuid,name,attr,size,free,extent_size,extent_count,free_count,"
             "tags,vg_mda_size,vg_mda_free,lv_count,pv_count,pv_name")
LV_FIELDS = "uuid,name,vg_name,attr,size,seg_start_pe,devices,tags"

VG_ATTR_BITS = ("permission", "resizeable", "exported",
                "partial", "allocation", "clustered")
LV_ATTR_BITS = ("voltype", "permission", "allocations", "fixedminor", "state",
                "devopen", "target", "zero")

# Returned by lvm commands for pv_name when a pv name is not available.
UNKNOWN_DEVICE = "unknown device"

PV = namedtuple("PV", PV_FIELDS + ",guid")
VG = namedtuple("VG", VG_FIELDS + ",writeable,partial")
VG_ATTR = namedtuple("VG_ATTR", VG_ATTR_BITS)
LV = namedtuple("LV", LV_FIELDS + ",writeable,opened,active")
LV_ATTR = namedtuple("LV_ATTR", LV_ATTR_BITS)
Stub = namedtuple("Stub", "name, stale")


class Unreadable(Stub):
    __slots__ = ()

    def __getattr__(self, attrName):
        log.warning("%s can't be reloaded, please check your storage "
                    "connections.", self.name)
        raise AttributeError("Failed reload: %s" % self.name)

# VG states
VG_OK = "OK"
VG_PARTIAL = "PARTIAL"
VG_UNKNOWN = "UNKNOWN"

SEPARATOR = "|"
LVM_NOBACKUP = ("--autobackup", "n")
LVM_FLAGS = ("--noheadings", "--units", "b", "--nosuffix", "--separator",
             SEPARATOR, "--ignoreskippedcluster")

PV_PREFIX = "/dev/mapper"
# Assuming there are no spaces in the PV name
re_pvName = re.compile(PV_PREFIX + '[^\s\"]+', re.MULTILINE)

# operations lock
LVM_OP_INVALIDATE = "lvm invalidate operation"
LVM_OP_RELOAD = "lvm reload operation"

PVS_CMD = ("pvs",) + LVM_FLAGS + ("-o", PV_FIELDS)
VGS_CMD = ("vgs",) + LVM_FLAGS + ("-o", VG_FIELDS)
LVS_CMD = ("lvs",) + LVM_FLAGS + ("-o", LV_FIELDS)

# FIXME we must use different METADATA_USER ownership for qemu-unreadable
# metadata volumes
USER_GROUP = constants.DISKIMAGE_USER + ":" + constants.DISKIMAGE_GROUP

LVMCONF_TEMPLATE = """
devices {
preferred_names = ["^/dev/mapper/"]
ignore_suspended_devices=1
write_cache_state=0
disable_after_error_count=3
obtain_device_list_from_udev=0
%s
}

global {
 locking_type=1
 prioritise_write_locks=1
 wait_for_locks=1
 use_lvmetad=0
}

backup {
 retain_min = 50
 retain_days = 0
}
"""

VAR_RUN_VDSM = constants.P_VDSM_RUN
VDSM_LVM_SYSTEM_DIR = os.path.join(VAR_RUN_VDSM, "lvm")
VDSM_LVM_CONF = os.path.join(VDSM_LVM_SYSTEM_DIR, "lvm.conf")

USER_DEV_LIST = filter(None, config.get("irs", "lvm_dev_whitelist").split(","))


def _buildFilter(devices):
    strippeds = set(d.strip() for d in devices)
    strippeds.discard('')  # Who has put a blank here?
    strippeds = sorted(strippeds)
    dmPaths = [dev.replace(r'\x', r'\\x') for dev in strippeds]
    filt = '|'.join(dmPaths)
    if len(filt) > 0:
        filt = "'a|" + filt + "|', "

    filt = "filter = [ " + filt + "'r|.*|' ]"
    return filt


def _buildConfig(devList):
    flt = _buildFilter(chain(devList, USER_DEV_LIST))
    conf = LVMCONF_TEMPLATE % flt
    return conf.replace("\n", " ")


def _updateLvmConf(conf):
    # Make a convenience copy for the debugging purposes
    try:
        if not os.path.isdir(VDSM_LVM_SYSTEM_DIR):
            os.mkdir(VDSM_LVM_SYSTEM_DIR)

        with open(VDSM_LVM_CONF, "w") as lvmconf:
            lvmconf.write(conf)

    except IOError as e:
        # We are not interested in exceptions here, note it and
        log.warning("Cannot create %s file %s", VDSM_LVM_CONF, str(e))


#
# Make sure that "args" is suitable for consumption in interfaces
# that expect an iterabale argument. The string is treated a single
# argument an converted into list, containing that string.
# Strings have not __iter__ attribute.
#
def _normalizeargs(args=None):
    if args is None:
        args = []
    elif not hasattr(args, "__iter__"):
        args = [args]

    return args


def _tags2Tuple(sTags):
    """
    Tags comma separated string as a list.

    Return an empty tuple for sTags == ""
    """
    return tuple(sTags.split(",")) if sTags else tuple()


def makePV(*args):
    guid = os.path.basename(args[1])
    args += (guid,)
    return PV(*args)


def makeVG(*args):
    args = list(args)
    # Convert tag string into tuple.
    tags = _tags2Tuple(args[VG._fields.index("tags")])
    args[VG._fields.index("tags")] = tags
    # Convert attr string into named tuple fields.
    # tuple("wz--n-") = ('w', 'z', '-', '-', 'n', '-')
    sAttr = args[VG._fields.index("attr")]
    attr_values = tuple(sAttr[:len(VG_ATTR._fields)])
    attrs = VG_ATTR(*attr_values)
    args[VG._fields.index("attr")] = attrs
    # Convert pv_names list to tuple.
    args[VG._fields.index("pv_name")] = \
        tuple(args[VG._fields.index("pv_name")])
    # Add properties. Should be ordered as VG_PROPERTIES.
    args.append(attrs.permission == "w")  # Writable
    args.append(VG_OK if attrs.partial == "-" else VG_PARTIAL)  # Partial
    return VG(*args)


def makeLV(*args):
    args = list(args)
    # Convert tag string into tuple.
    tags = _tags2Tuple(args[LV._fields.index("tags")])
    args[LV._fields.index("tags")] = tags
    # Convert attr string into named tuple fields.
    sAttr = args[LV._fields.index("attr")]
    attr_values = tuple(sAttr[:len(LV_ATTR._fields)])
    attrs = LV_ATTR(*attr_values)
    args[LV._fields.index("attr")] = attrs
    # Add properties. Should be ordered as VG_PROPERTIES.
    args.append(attrs.permission == "w")  # writable
    args.append(attrs.devopen == "o")     # opened
    args.append(attrs.state == "a")       # active
    return LV(*args)


class LVMCache(object):
    """
    Keep all the LVM information.
    """

    def _getCachedExtraCfg(self):
        if not self._filterStale:
            return self._extraCfg

        with self._filterLock:
            if not self._filterStale:
                return self._extraCfg

            self._extraCfg = _buildConfig(multipath.getMPDevNamesIter())
            _updateLvmConf(self._extraCfg)
            self._filterStale = False

            return self._extraCfg

    def _addExtraCfg(self, cmd, devices=tuple()):
        newcmd = [constants.EXT_LVM, cmd[0]]
        if devices:
            conf = _buildConfig(devices)
        else:
            conf = self._getCachedExtraCfg()

        newcmd += ["--config", conf]

        if len(cmd) > 1:
            newcmd += cmd[1:]

        return newcmd

    def invalidateFilter(self):
        self._filterStale = True

    def invalidateCache(self):
        self.invalidateFilter()
        self.flush()

    def __init__(self):
        self._filterStale = True
        self._extraCfg = None
        self._filterLock = threading.Lock()
        self._oplock = misc.OperationMutex()
        self._stalepv = True
        self._stalevg = True
        self._stalelv = True
        self._pvs = {}
        self._vgs = {}
        self._lvs = {}

    def cmd(self, cmd, devices=tuple()):
        finalCmd = self._addExtraCfg(cmd, devices)
        rc, out, err = misc.execCmd(finalCmd, sudo=True)
        if rc != 0:
            # Filter might be stale
            self.invalidateFilter()
            newCmd = self._addExtraCfg(cmd)
            # Before blindly trying again make sure
            # that the commands are not identical, because
            # the devlist is sorted there is no fear
            # of two identical filters looking differently
            if newCmd != finalCmd:
                return misc.execCmd(newCmd, sudo=True)

        return rc, out, err

    def __str__(self):
        return ("PVS:\n%s\n\nVGS:\n%s\n\nLVS:\n%s" %
                (pp.pformat(self._pvs),
                 pp.pformat(self._vgs),
                 pp.pformat(self._lvs)))

    def bootstrap(self):
        self._reloadpvs()
        self._reloadvgs()
        self._reloadAllLvs()

    def _reloadpvs(self, pvName=None):
        cmd = list(PVS_CMD)
        pvNames = _normalizeargs(pvName)
        cmd.extend(pvNames)
        with self._oplock.acquireContext(LVM_OP_RELOAD):
            rc, out, err = self.cmd(cmd)
            if rc != 0:
                log.warning("lvm pvs failed: %s %s %s", str(rc), str(out),
                            str(err))
                pvNames = pvNames if pvNames else self._pvs.keys()
                for p in pvNames:
                    if isinstance(self._pvs.get(p), Stub):
                        self._pvs[p] = Unreadable(self._pvs[p].name, True)
                return dict(self._pvs)

            updatedPVs = {}
            for line in out:
                fields = [field.strip() for field in line.split(SEPARATOR)]
                pv = makePV(*fields)
                if pv.name == UNKNOWN_DEVICE:
                    log.error("Missing pv: %s in vg: %s", pv.uuid, pv.vg_name)
                    continue
                self._pvs[pv.name] = pv
                updatedPVs[pv.name] = pv
            # If we updated all the PVs drop stale flag
            if not pvName:
                self._stalepv = False
                # Remove stalePVs
                stalePVs = [staleName for staleName in self._pvs.keys()
                            if staleName not in updatedPVs.iterkeys()]
                for staleName in stalePVs:
                    log.warning("Removing stale PV: %s", staleName)
                    self._pvs.pop((staleName), None)

        return updatedPVs

    def _getVGDevs(self, vgNames):
        devices = []
        for name in vgNames:
            try:
                pvs = self._vgs[name].pv_name  # pv_names tuple
            except (KeyError, AttributeError):  # Yet unknown VG, stub
                devices = tuple()
                break  # unknownVG = True
            else:
                devices.extend(pvs)
        else:  # All known VGs
            devices = tuple(devices)
        return devices

    def _reloadvgs(self, vgName=None):
        cmd = list(VGS_CMD)
        vgNames = _normalizeargs(vgName)
        cmd.extend(vgNames)

        with self._oplock.acquireContext(LVM_OP_RELOAD):
            rc, out, err = self.cmd(cmd, self._getVGDevs(vgNames))

            if rc != 0:
                log.warning("lvm vgs failed: %s %s %s", str(rc), str(out),
                            str(err))
                vgNames = vgNames if vgNames else self._vgs.keys()
                for v in vgNames:
                    if isinstance(self._vgs.get(v), Stub):
                        self._vgs[v] = Unreadable(self._vgs[v].name, True)

            if not len(out):
                return dict(self._vgs)

            updatedVGs = {}
            vgsFields = {}
            for line in out:
                fields = [field.strip() for field in line.split(SEPARATOR)]
                uuid = fields[VG._fields.index("uuid")]
                pvNameIdx = VG._fields.index("pv_name")
                pv_name = fields[pvNameIdx]
                if pv_name == UNKNOWN_DEVICE:
                    # PV is missing, e.g. device lost of target not connected
                    continue
                if uuid not in vgsFields:
                    fields[pvNameIdx] = [pv_name]  # Make a pv_names list
                    vgsFields[uuid] = fields
                else:
                    vgsFields[uuid][pvNameIdx].append(pv_name)
            for fields in vgsFields.itervalues():
                vg = makeVG(*fields)
                if int(vg.pv_count) != len(vg.pv_name):
                    log.error("vg %s has pv_count %s but pv_names %s",
                              vg.name, vg.pv_count, vg.pv_name)
                self._vgs[vg.name] = vg
                updatedVGs[vg.name] = vg
            # If we updated all the VGs drop stale flag
            if not vgName:
                self._stalevg = False
                # Remove stale VGs
                staleVGs = [staleName for staleName in self._vgs.keys()
                            if staleName not in updatedVGs.iterkeys()]
                for staleName in staleVGs:
                    removeVgMapping(staleName)
                    log.warning("Removing stale VG: %s", staleName)
                    self._vgs.pop((staleName), None)

        return updatedVGs

    def _reloadlvs(self, vgName, lvNames=None):
        lvNames = _normalizeargs(lvNames)
        cmd = list(LVS_CMD)
        if lvNames:
            cmd.extend(["%s/%s" % (vgName, lvName) for lvName in lvNames])
        else:
            cmd.append(vgName)

        with self._oplock.acquireContext(LVM_OP_RELOAD):
            rc, out, err = self.cmd(cmd, self._getVGDevs((vgName, )))

            if rc != 0:
                log.warning("lvm lvs failed: %s %s %s", str(rc), str(out),
                            str(err))
                lvNames = lvNames if lvNames else self._lvs.keys()
                for l in lvNames:
                    if isinstance(self._lvs.get(l), Stub):
                        self._lvs[l] = Unreadable(self._lvs[l].name, True)
                return dict(self._lvs)

            updatedLVs = {}
            for line in out:
                fields = [field.strip() for field in line.split(SEPARATOR)]
                lv = makeLV(*fields)
                # For LV we are only interested in its first extent
                if lv.seg_start_pe == "0":
                    self._lvs[(lv.vg_name, lv.name)] = lv
                    updatedLVs[(lv.vg_name, lv.name)] = lv

            # Determine if there are stale LVs
            if lvNames:
                staleLVs = (lvName for lvName in lvNames
                            if (vgName, lvName) not in updatedLVs.iterkeys())
            else:
                # All the LVs in the VG
                staleLVs = (lvName for v, lvName in self._lvs.keys()
                            if (v == vgName) and
                            ((vgName, lvName) not in updatedLVs.iterkeys()))

            for lvName in staleLVs:
                log.warning("Removing stale lv: %s/%s", vgName, lvName)
                self._lvs.pop((vgName, lvName), None)

            log.debug("lvs reloaded")

        return updatedLVs

    def _reloadAllLvs(self):
        """
        Used only during bootstrap.
        """
        cmd = list(LVS_CMD)
        rc, out, err = self.cmd(cmd)
        if rc == 0:
            updatedLVs = set()
            for line in out:
                fields = [field.strip() for field in line.split(SEPARATOR)]
                lv = makeLV(*fields)
                # For LV we are only interested in its first extent
                if lv.seg_start_pe == "0":
                    self._lvs[(lv.vg_name, lv.name)] = lv
                    updatedLVs.add((lv.vg_name, lv.name))

            # Remove stales
            for vgName, lvName in self._lvs.keys():
                if (vgName, lvName) not in updatedLVs:
                    self._lvs.pop((vgName, lvName), None)
                    log.error("Removing stale lv: %s/%s", vgName, lvName)
            self._stalelv = False
        return dict(self._lvs)

    def _invalidatepvs(self, pvNames):
        with self._oplock.acquireContext(LVM_OP_INVALIDATE):
            pvNames = _normalizeargs(pvNames)
            for pvName in pvNames:
                self._pvs[pvName] = Stub(pvName, True)

    def _invalidateAllPvs(self):
        with self._oplock.acquireContext(LVM_OP_INVALIDATE):
            self._stalepv = True
            self._pvs.clear()

    def _invalidatevgs(self, vgNames):
        vgNames = _normalizeargs(vgNames)
        with self._oplock.acquireContext(LVM_OP_INVALIDATE):
            for vgName in vgNames:
                self._vgs[vgName] = Stub(vgName, True)

    def _invalidateAllVgs(self):
        with self._oplock.acquireContext(LVM_OP_INVALIDATE):
            self._stalevg = True
            self._vgs.clear()

    def _invalidatelvs(self, vgName, lvNames=None):
        with self._oplock.acquireContext(LVM_OP_INVALIDATE):
            lvNames = _normalizeargs(lvNames)
            # Invalidate LVs in a specific VG
            if lvNames:
                # Invalidate a specific LVs
                for lvName in lvNames:
                    self._lvs[(vgName, lvName)] = Stub(lvName, True)
            else:
                # Invalidate all the LVs in a given VG
                for lv in self._lvs.values():
                    if not isinstance(lv, Stub):
                        if lv.vg_name == vgName:
                            self._lvs[(vgName, lv.name)] = Stub(lv.name, True)

    def _invalidateAllLvs(self):
        with self._oplock.acquireContext(LVM_OP_INVALIDATE):
            self._stalelv = True
            self._lvs.clear()

    def flush(self):
        self._invalidateAllPvs()
        self._invalidateAllVgs()
        self._invalidateAllLvs()

    def getPv(self, pvName):
        # Get specific PV
        pv = self._pvs.get(pvName)
        if not pv or isinstance(pv, Stub):
            pvs = self._reloadpvs(pvName)
            pv = pvs.get(pvName)
        return pv

    def getAllPvs(self):
        # Get everything we have
        if self._stalepv:
            pvs = self._reloadpvs()
        else:
            pvs = dict(self._pvs)
            stalepvs = [pv.name for pv in pvs.itervalues()
                        if isinstance(pv, Stub)]
            if stalepvs:
                reloaded = self._reloadpvs(stalepvs)
                pvs.update(reloaded)
        return pvs.values()

    def getVg(self, vgName):
        # Get specific VG
        vg = self._vgs.get(vgName)
        if not vg or isinstance(vg, Stub):
            vgs = self._reloadvgs(vgName)
            vg = vgs.get(vgName)
        return vg

    def getVgs(self, vgNames):
        """Reloads all the VGs of the set.

        Can block for suspended devices.
        Fills the cache but not uses it.
        Only returns found VGs.
        """
        return [vg for vgName, vg in self._reloadvgs(vgNames).iteritems()
                if vgName in vgNames]

    def getAllVgs(self):
        # Get everything we have
        if self._stalevg:
            vgs = self._reloadvgs()
        else:
            vgs = dict(self._vgs)
            stalevgs = [vg.name for vg in vgs.itervalues()
                        if isinstance(vg, Stub)]
            if stalevgs:
                reloaded = self._reloadvgs(stalevgs)
                vgs.update(reloaded)
        return vgs.values()

    def getLv(self, vgName, lvName=None):
        # Checking self._stalelv here is suboptimal, because
        # unnecessary reloads
        # are done.

        # Return vgName/lvName info
        # If both 'vgName' and 'lvName' are None then return everything
        # If only 'lvName' is None then return all the LVs in the given VG
        # If only 'vgName' is None it is weird, so return nothing
        # (we can consider returning all the LVs with a given name)
        if lvName:
            # vgName, lvName
            lv = self._lvs.get((vgName, lvName))
            if not lv or isinstance(lv, Stub):
                # while we here reload all the LVs in the VG
                lvs = self._reloadlvs(vgName)
                lv = lvs.get((vgName, lvName))
                if not lv:
                    log.warning("lv: %s not found in lvs vg: %s response",
                                lvName, vgName)
            res = lv
        else:
            # vgName, None
            # If there any stale LVs reload the whole VG, since it would
            # cost us around same efforts anyhow and these stale LVs can
            # be in the vg.
            # Will be better when the pvs dict will be part of the vg.
            # Fix me: should not be more stubs
            if self._stalelv or any(isinstance(lv, Stub)
                                    for lv in self._lvs.values()):
                lvs = self._reloadlvs(vgName)
            else:
                lvs = dict(self._lvs)
            # lvs = self._reloadlvs()
            lvs = [lv for lv in lvs.values()
                   if not isinstance(lv, Stub) and (lv.vg_name == vgName)]
            res = lvs
        return res

    def getAllLvs(self):
        # None, None
        if self._stalelv or any(isinstance(lv, Stub)
                                for lv in self._lvs.values()):
            lvs = self._reloadAllLvs()
        else:
            lvs = dict(self._lvs)
        return lvs.values()

_lvminfo = LVMCache()


def bootstrap(refreshlvs=()):
    """
    Bootstrap lvm module

    This function builds the lvm cache and ensure that all unused lvs are
    deactivated, expect lvs matching refreshlvs, which are refreshed instead.
    """
    _lvminfo.bootstrap()

    refreshlvs = set(refreshlvs)

    for vg in _lvminfo.getAllVgs():
        deactivate = []
        refresh = []

        for lv in _lvminfo.getLv(vg.name):
            if lv.active:
                if lv.name in refreshlvs:
                    refresh.append(lv.name)
                elif lv.opened:
                    log.debug("Skipping open lv: vg=%s lv=%s", vg.name,
                              lv.name)
                else:
                    deactivate.append(lv.name)

        if deactivate:
            log.info("Deactivating lvs: vg=%s lvs=%s", vg.name, deactivate)
            try:
                _setLVAvailability(vg.name, deactivate, "n")
            except se.CannotDeactivateLogicalVolume:
                log.error("Error deactivating lvs: vg=%s lvs=%s", vg.name,
                          deactivate)
            # Some lvs are inactive now
            _lvminfo._invalidatelvs(vg.name, deactivate)

        if refresh:
            log.info("Refreshing lvs: vg=%s lvs=%s", vg.name, refresh)
            try:
                refreshLVs(vg.name, refresh)
            except se.LogicalVolumeRefreshError:
                log.error("Error refreshing lvs: vg=%s lvs=%s", vg.name,
                          refresh)


def invalidateCache():
    _lvminfo.invalidateCache()


def _fqpvname(pv):
    if pv and not pv.startswith(PV_PREFIX):
        pv = os.path.join(PV_PREFIX, pv)
    return pv


def _createpv(devices, metadataSize, options=tuple()):
    """
    Size for pvcreate should be with units k|m|g
    pvcreate on a dev that is already a PV but not in a VG returns rc = 0.
    The device is re-created with the new parameters.
    """
    cmd = ["pvcreate"]
    if options:
        cmd.extend(options)
    if metadataSize != 0:
        cmd.extend(("--metadatasize", "%sm" % metadataSize,
                    "--metadatacopies", "2",
                    "--metadataignore", "y"))
    cmd.extend(devices)
    rc, out, err = _lvminfo.cmd(cmd, devices)
    return rc, out, err


def _initpvs(devices, metadataSize, force=False):

    def _initpvs_removeHolders():
        """Remove holders for all devices."""
        for device in devices:
            try:
                devicemapper.removeMappingsHoldingDevice(
                    os.path.basename(device))
            except OSError as e:
                if e.errno == errno.ENODEV:
                    raise se.PhysDevInitializationError("%s: %s" %
                                                        (device, str(e)))
                else:
                    raise

    if force is True:
        options = ("-y", "-ff")
        _initpvs_removeHolders()
    else:
        options = tuple()

    rc, out, err = _createpv(devices, metadataSize, options)
    _lvminfo._invalidatepvs(devices)
    if rc != 0:
        log.error("pvcreate failed with rc=%s", rc)
        log.error("%s, %s", out, err)
        raise se.PhysDevInitializationError(str(devices))

    return (set(devices), set(), rc, out, err)


def getLvDmName(vgName, lvName):
    return "%s-%s" % (vgName.replace("-", "--"), lvName)


def removeVgMapping(vgName):
    """
    Removes the mapping of the specified volume group.
    Utilizes the fact that the mapping created by the LVM looks like that
    e45c12b0--f520--498a--82bb--c6cb294b990f-master
    i.e vg name concatenated with volume name (dash is escaped with dash)
    """
    mappingPrefix = getLvDmName(vgName, "")
    mappings = devicemapper.getAllMappedDevices()

    for mapping in mappings:
        if not mapping.startswith(mappingPrefix):
            continue
        try:
            devicemapper.removeMapping(mapping)
        except Exception:
            pass


# Activation of the whole vg is assumed to be used nowhere.
# This is a separate function just in case.
def _setVgAvailability(vgs, available):
    vgs = _normalizeargs(vgs)
    cmd = ["vgchange", "--available", available] + vgs
    rc, out, err = _lvminfo.cmd(cmd, _lvminfo._getVGDevs(vgs))
    for vg in vgs:
        _lvminfo._invalidatelvs(vg)
    if rc != 0:
        # During deactivation, in vg.py (sic):
        # we ignore error here because we don't care about this vg anymore
        if available == "n":
            log.info("deactivate vg %s failed: rc %s - %s %s (ignored)" %
                     (vgs, rc, out, err))
        else:
            raise se.VolumeGroupActionError(
                "vgchange on vg(s) %s failed. %d %s %s" % (vgs, rc, out, err))


def changelv(vg, lvs, attrs):
    """
    Change multiple attributes on multiple LVs.

    vg: VG name
    lvs: a single LV name or iterable of LV names.
    attrs: an iterable of (attr, value) pairs),
            e.g. (('--available', 'y'), ('--permission', 'rw')

    Note:
    You may activate an activated LV without error
    but lvchange returns an error (RC=5) when activating rw if already rw
    """

    lvs = _normalizeargs(lvs)
    # If it fails or not we (may be) change the lv,
    # so we invalidate cache to reload these volumes on first occasion
    lvnames = tuple("%s/%s" % (vg, lv) for lv in lvs)
    cmd = ["lvchange"]
    cmd.extend(LVM_NOBACKUP)
    if isinstance(attrs[0], str):
        # ("--attribute", "value")
        cmd.extend(attrs)
    else:
        # (("--aa", "v1"), ("--ab", "v2"))
        for attr in attrs:
            cmd.extend(attr)
    cmd.extend(lvnames)
    rc, out, err = _lvminfo.cmd(tuple(cmd), _lvminfo._getVGDevs((vg, )))
    _lvminfo._invalidatelvs(vg, lvs)
    if rc != 0 and len(out) < 1:
        raise se.StorageException("%d %s %s\n%s/%s" % (rc, out, err, vg, lvs))


def _setLVAvailability(vg, lvs, available):
    try:
        changelv(vg, lvs, ("--available", available))
    except se.StorageException as e:
        error = ({"y": se.CannotActivateLogicalVolumes,
                  "n": se.CannotDeactivateLogicalVolume}
                 .get(available, se.VolumeGroupActionError))
        raise error(str(e))

#
# Public Object Accessors
#


def getPV(pvName):
    pv = _lvminfo.getPv(_fqpvname(pvName))
    if pv is None:
        raise se.InaccessiblePhysDev((pvName,))
    return pv


def getAllPVs():
    return _lvminfo.getAllPvs()


def testPVCreate(devices, metadataSize):
    """
    Only tests the pv creation.

    Should not affect the cache state.

    Receives guids iterable.
    Returns (un)pvables, (un)succeed guids.
    """
    devs = tuple("%s/%s" % (PV_PREFIX, dev) for dev in devices)

    options = ("--test",)
    rc, out, err = _createpv(devs, metadataSize, options)
    if rc == 0:
        unusedDevs = set(devices)
        usedDevs = set()
    else:
        unusedDevs = set(re_pvName.findall("\n".join(out)))
        usedDevs = set(devs) - set(unusedDevs)
        log.debug("rc: %s, out: %s, err: %s, unusedDevs: %s, usedDevs: %s",
                  rc, out, err, unusedDevs, usedDevs)

    return unusedDevs, usedDevs


def resizePV(vgName, guid):
    """
    In case the LUN was increased on storage server, in order to see the
    changes it is needed to resize the PV after the multipath devices have
    been resized

    Raises se.CouldNotResizePhysicalVolume if pvresize fails
    """
    pvName = _fqpvname(guid)
    cmd = ["pvresize", pvName]
    rc, out, err = _lvminfo.cmd(cmd, _lvminfo._getVGDevs((vgName, )))
    if rc != 0:
        raise se.CouldNotResizePhysicalVolume(pvName, err)
    _lvminfo._invalidatepvs(pvName)
    _lvminfo._invalidatevgs(vgName)


def getVG(vgName):
    vg = _lvminfo.getVg(vgName)  # returns single VG namedtuple
    if not vg:
        raise se.VolumeGroupDoesNotExist(vgName)
    else:
        return vg


def getVGs(vgNames):
    return _lvminfo.getVgs(vgNames)  # returns list


def getAllVGs():
    return _lvminfo.getAllVgs()  # returns list


# TODO: lvm VG UUID should not be exposed.
# Remove this function when hsm.public_createVG is removed.
def getVGbyUUID(vgUUID):
    # cycle through all the VGs until the one with the given UUID found
    for vg in getAllVGs():
        try:
            if vg.uuid == vgUUID:
                return vg
        except AttributeError as e:
            # An unreloadable VG found but may be we are not looking for it.
            log.debug("%s" % e.message, exc_info=True)
            continue
    # If not cry loudly
    raise se.VolumeGroupDoesNotExist("vg_uuid: %s" % vgUUID)


def getLV(vgName, lvName=None):
    lv = _lvminfo.getLv(vgName, lvName)
    # getLV() should not return None
    if not lv:
        raise se.LogicalVolumeDoesNotExistError("%s/%s" % (vgName, lvName))
    else:
        return lv


#
# Public Volume Group interface
#

def createVG(vgName, devices, initialTag, metadataSize, extentsize="128m",
             force=False):
    pvs = [_fqpvname(pdev) for pdev in _normalizeargs(devices)]
    _checkpvsblksize(pvs)

    _initpvs(pvs, metadataSize, force)
    # Activate the 1st PV metadata areas
    cmd = ["pvchange", "--metadataignore", "n"]
    cmd.append(pvs[0])
    rc, out, err = _lvminfo.cmd(cmd, tuple(pvs))
    if rc != 0:
        raise se.PhysDevInitializationError(pvs[0])

    options = ["--physicalextentsize", extentsize]
    if initialTag:
        options.extend(("--addtag", initialTag))
    cmd = ["vgcreate"] + options + [vgName] + pvs
    rc, out, err = _lvminfo.cmd(cmd, tuple(pvs))
    if rc == 0:
        _lvminfo._invalidatepvs(pvs)
        _lvminfo._invalidatevgs(vgName)
        log.debug("Cache after createvg %s", _lvminfo._vgs)
    else:
        raise se.VolumeGroupCreateError(vgName, pvs)


def removeVG(vgName):
    cmd = ["vgremove", "-f", vgName]
    rc, out, err = _lvminfo.cmd(cmd, _lvminfo._getVGDevs((vgName, )))
    pvs = tuple(pvName for pvName, pv in _lvminfo._pvs.iteritems()
                if not isinstance(pv, Stub) and pv.vg_name == vgName)
    # PVS needs to be reloaded anyhow: if vg is removed they are staled,
    # if vg remove failed, something must be wrong with devices and we want
    # cache updated as well
    _lvminfo._invalidatepvs(pvs)
    # If vgremove failed reintroduce the VG into the cache
    if rc != 0:
        _lvminfo._invalidatevgs(vgName)
        raise se.VolumeGroupRemoveError("VG %s remove failed." % vgName)
    else:
        # Remove the vg from the cache
        _lvminfo._vgs.pop(vgName, None)


def removeVGbyUUID(vgUUID):
    vg = getVGbyUUID(vgUUID)
    if vg:
        removeVG(vg.name)


def extendVG(vgName, devices, force):
    pvs = [_fqpvname(pdev) for pdev in _normalizeargs(devices)]
    _checkpvsblksize(pvs, getVGBlockSizes(vgName))
    vg = _lvminfo.getVg(vgName)
    # Format extension PVs as all the other already in the VG
    _initpvs(pvs, int(vg.vg_mda_size) / 2 ** 20, force)

    cmd = ["vgextend", vgName] + pvs
    devs = tuple(_lvminfo._getVGDevs((vgName, )) + tuple(pvs))
    rc, out, err = _lvminfo.cmd(cmd, devs)
    if rc == 0:
        _lvminfo._invalidatepvs(pvs)
        _lvminfo._invalidatevgs(vgName)
        log.debug("Cache after extending vg %s", _lvminfo._vgs)
    else:
        raise se.VolumeGroupExtendError(vgName, pvs)


def chkVG(vgName):
    cmd = ["vgck", vgName]
    rc, out, err = _lvminfo.cmd(cmd, _lvminfo._getVGDevs((vgName, )))
    if rc != 0:
        _lvminfo._invalidatevgs(vgName)
        _lvminfo._invalidatelvs(vgName)
        raise se.StorageDomainAccessError("%s: %s" % (vgName, err))
    return True


def deactivateVG(vgName):
    getVG(vgName)  # Check existence
    _setVgAvailability(vgName, available="n")


def invalidateVG(vgName):
    _lvminfo._invalidatevgs(vgName)
    _lvminfo._invalidatelvs(vgName)


def _getpvblksize(pv):
    dev = devicemapper.getDmId(os.path.basename(pv))
    return multipath.getDeviceBlockSizes(dev)


def _checkpvsblksize(pvs, vgBlkSize=None):
    for pv in pvs:
        pvBlkSize = _getpvblksize(pv)
        logPvBlkSize, phyPvBlkSize = pvBlkSize

        if logPvBlkSize not in constants.SUPPORTED_BLOCKSIZE:
            raise se.DeviceBlockSizeError(pvBlkSize)

        if phyPvBlkSize < logPvBlkSize:
            raise se.DeviceBlockSizeError(pvBlkSize)

        # WARN: This is setting vgBlkSize to the first value found by
        #       _getpvblksize (if not provided by the function call).
        #       It makes sure that all the PVs have the same block size.
        if vgBlkSize is None:
            vgBlkSize = pvBlkSize

        if logPvBlkSize != vgBlkSize[0]:
            raise se.VolumeGroupBlockSizeError(vgBlkSize, pvBlkSize)


def checkVGBlockSizes(vgUUID, vgBlkSize=None):
    pvs = listPVNames(vgUUID)
    if not pvs:
        raise se.VolumeGroupDoesNotExist("vg_uuid: %s" % vgUUID)
    _checkpvsblksize(pvs, vgBlkSize)


def getVGBlockSizes(vgUUID):
    pvs = listPVNames(vgUUID)
    if not pvs:
        raise se.VolumeGroupDoesNotExist("vg_uuid: %s" % vgUUID)
    # Returning the block size of the first pv is correct since we don't allow
    # devices with different block size to be on the same VG.
    return _getpvblksize(pvs[0])

#
# Public Logical volume interface
#


def createLV(vgName, lvName, size, activate=True, contiguous=False,
             initialTag=None):
    """
    Size units: MB (1024 ** 2 = 2 ** 20)B.
    """
    # WARNING! From man vgs:
    # All sizes are output in these units: (h)uman-readable, (b)ytes,
    # (s)ectors, (k)ilobytes, (m)egabytes, (g)igabytes, (t)erabytes,
    # (p)etabytes, (e)xabytes.
    # Capitalise to use multiples of 1000 (S.I.) instead of 1024.

    cont = {True: "y", False: "n"}[contiguous]
    cmd = ["lvcreate"]
    cmd.extend(LVM_NOBACKUP)
    cmd.extend(("--contiguous", cont, "--size", "%sm" % size))
    if initialTag is not None:
        cmd.extend(("--addtag", initialTag))
    cmd.extend(("--name", lvName, vgName))
    rc, out, err = _lvminfo.cmd(cmd, _lvminfo._getVGDevs((vgName, )))

    if rc == 0:
        _lvminfo._invalidatevgs(vgName)
        _lvminfo._invalidatelvs(vgName, lvName)
    else:
        raise se.CannotCreateLogicalVolume(vgName, lvName)

    # TBD: Need to explore the option of running lvcreate w/o devmapper
    # so that if activation is not needed it would be skipped in the
    # first place
    if activate:
        lv_path = lvPath(vgName, lvName)
        st = os.stat(lv_path)
        uName = pwd.getpwuid(st.st_uid).pw_name
        gName = grp.getgrgid(st.st_gid).gr_name
        if ":".join((uName, gName)) != USER_GROUP:
            cmd = [constants.EXT_CHOWN, USER_GROUP, lv_path]
            if misc.execCmd(cmd, sudo=True)[0] != 0:
                log.warning("Could not change ownership of one or more "
                            "volumes in vg (%s) - %s", vgName, lvName)
    else:
        _setLVAvailability(vgName, lvName, "n")


def removeLVs(vgName, lvNames):
    lvNames = _normalizeargs(lvNames)
    # Assert that the LVs are inactive before remove.
    for lvName in lvNames:
        if _isLVActive(vgName, lvName):
            # Fix me
            # Should not remove active LVs
            # raise se.CannotRemoveLogicalVolume(vgName, lvName)
            log.warning("Removing active volume %s/%s" % (vgName, lvName))

    # LV exists or not in cache, attempting to remove it.
    # Removing Stubs also. Active Stubs should raise.
    # Destroy LV
    # Fix me:removes active LVs too. "-f" should be removed.
    cmd = ["lvremove", "-f"]
    cmd.extend(LVM_NOBACKUP)
    for lvName in lvNames:
        cmd.append("%s/%s" % (vgName, lvName))
    rc, out, err = _lvminfo.cmd(cmd, _lvminfo._getVGDevs((vgName, )))
    if rc == 0:
        for lvName in lvNames:
            # Remove the LV from the cache
            _lvminfo._lvs.pop((vgName, lvName), None)
            # If lvremove succeeded it affected VG as well
            _lvminfo._invalidatevgs(vgName)
    else:
        # Otherwise LV info needs to be refreshed
        _lvminfo._invalidatelvs(vgName, lvNames)
        raise se.CannotRemoveLogicalVolume(vgName, str(lvNames))


def _resizeLV(op, vgName, lvName, size):
    """
    Size units: MB (1024 ** 2 = 2 ** 20)B.
    """
    # WARNING! From man vgs:
    # All sizes are output in these units: (h)uman-readable, (b)ytes,
    # (s)ectors,(k)ilobytes, (m)egabytes, (g)igabytes, (t)erabytes,
    # (p)etabytes, (e)xabytes.
    # Capitalise to use multiples of 1000 (S.I.) instead of 1024.
    cmd = (op,) + LVM_NOBACKUP
    cmd += ("--size", "%sm" % (size,), "%s/%s" % (vgName, lvName))
    rc, out, err = _lvminfo.cmd(cmd, _lvminfo._getVGDevs((vgName, )))
    if rc == 0:
        _lvminfo._invalidatevgs(vgName)
        _lvminfo._invalidatelvs(vgName, lvName)

    elif rc == 3:
        # In LVM we trust. Hope that 3 is only for this.
        log.debug("New size (in extents) matches existing size (in extents).")
    elif rc != 0:
        # get the free extents size
        # YaRC
        vg = getVG(vgName)
        free_size = int(vg.extent_size) * int(vg.free_count)  # in B
        if free_size < int(size) * constants.MEGAB:
            raise se.VolumeGroupSizeError("%s/%s %d > %d (MiB)" %
                                          (vgName, lvName, int(size),
                                           free_size / constants.MEGAB))

        raise se.LogicalVolumeExtendError(vgName, lvName, "%sM" % (size, ))


def extendLV(vgName, lvName, size):
    _resizeLV("lvextend", vgName, lvName, size)


def reduceLV(vgName, lvName, size):
    _resizeLV("lvreduce", vgName, lvName, size)


def activateLVs(vgName, lvNames):
    lvNames = _normalizeargs(lvNames)
    toActivate = [lvName for lvName in lvNames
                  if not _isLVActive(vgName, lvName)]
    if toActivate:
        _setLVAvailability(vgName, toActivate, "y")


def deactivateLVs(vgName, lvNames):
    lvNames = _normalizeargs(lvNames)
    toDeactivate = [lvName for lvName in lvNames
                    if _isLVActive(vgName, lvName)]
    if toDeactivate:
        _setLVAvailability(vgName, toDeactivate, "n")


def renameLV(vg, oldlv, newlv):
    cmd = ("lvrename",) + LVM_NOBACKUP + (vg, oldlv, newlv)
    rc, out, err = _lvminfo.cmd(cmd, _lvminfo._getVGDevs((vg, )))
    if rc != 0:
        raise se.LogicalVolumeRenameError("%s %s %s" % (vg, oldlv, newlv))

    _lvminfo._lvs.pop((vg, oldlv), None)
    _lvminfo._reloadlvs(vg, newlv)


def refreshLVs(vgName, lvNames):
    # If  the  logical  volumes  are active, reload their metadata.
    cmd = ['lvchange', '--refresh']
    cmd.extend("%s/%s" % (vgName, lv) for lv in lvNames)
    rc, out, err = _lvminfo.cmd(cmd, _lvminfo._getVGDevs((vgName, )))
    _lvminfo._invalidatelvs(vgName, lvNames)
    if rc != 0:
        raise se.LogicalVolumeRefreshError("%s failed" % list2cmdline(cmd))


# Fix me: Function name should mention LV or unify with VG version.
# may be for all the LVs in the whole VG?
def addtag(vg, lv, tag):
    lvname = "%s/%s" % (vg, lv)
    cmd = ("lvchange",) + LVM_NOBACKUP + ("--addtag", tag) + (lvname,)
    rc, out, err = _lvminfo.cmd(cmd, _lvminfo._getVGDevs((vg, )))
    _lvminfo._invalidatelvs(vg, lv)
    if rc != 0:
        # Fix me: should be se.ChangeLogicalVolumeError but this not exists.
        raise se.MissingTagOnLogicalVolume("%s/%s" % (vg, lv), tag)


def changeLVTags(vg, lv, delTags=(), addTags=()):
    lvname = '%s/%s' % (vg, lv)
    delTags = set(delTags)
    addTags = set(addTags)
    if delTags.intersection(addTags):
        raise se.LogicalVolumeReplaceTagError(
            "Cannot add and delete the same tag lv: `%s` tags: `%s`" %
            (lvname, ", ".join(delTags.intersection(addTags))))

    cmd = ['lvchange']
    cmd.extend(LVM_NOBACKUP)

    for tag in delTags:
        cmd.extend(("--deltag", tag))

    for tag in addTags:
        cmd.extend(('--addtag', tag))

    cmd.append(lvname)

    rc, out, err = _lvminfo.cmd(cmd, _lvminfo._getVGDevs((vg, )))
    _lvminfo._invalidatelvs(vg, lv)
    if rc != 0:
        raise se.LogicalVolumeReplaceTagError(
            'lv: `%s` add: `%s` del: `%s` (%s)' %
            (lvname, ", ".join(addTags), ", ".join(delTags), err[-1]))


def addLVTags(vg, lv, addTags):
    changeLVTags(vg, lv, addTags=addTags)


#
# Helper functions
#
def lvPath(vgName, lvName):
    return os.path.join("/dev", vgName, lvName)


def lvDmDev(vgName, lvName):
    """Return the LV dm device.

    returns: dm-X
    If the LV is inactive there is no dm device
    and OSError will be raised.
    """
    lvp = lvPath(vgName, lvName)
    return os.path.basename(os.readlink(lvp))


def _isLVActive(vgName, lvName):
    """Active volumes have a mp link.

    This function should not be used out of this module.
    """
    return os.path.exists(lvPath(vgName, lvName))


def changeVGTags(vgName, delTags=(), addTags=()):
    delTags = set(delTags)
    addTags = set(addTags)
    if delTags.intersection(addTags):
        raise se.VolumeGroupReplaceTagError(
            "Cannot add and delete the same tag vg: `%s` tags: `%s`" %
            (vgName, ", ".join(delTags.intersection(addTags))))

    cmd = ["vgchange"]

    for tag in delTags:
        cmd.extend(("--deltag", tag))
    for tag in addTags:
        cmd.extend(("--addtag", tag))

    cmd.append(vgName)
    rc, out, err = _lvminfo.cmd(cmd, _lvminfo._getVGDevs((vgName, )))
    _lvminfo._invalidatevgs(vgName)
    if rc != 0:
        raise se.VolumeGroupReplaceTagError(
            "vg:%s del:%s add:%s (%s)" %
            (vgName, ", ".join(delTags), ", ".join(addTags), err[-1]))


def replaceVGTag(vg, oldTag, newTag):
    changeVGTags(vg, [oldTag], [newTag])


def getFirstExt(vg, lv):
    return getLV(vg, lv).devices.strip(" )").split("(")


def listPVNames(vgName):
    try:
        pvNames = _lvminfo._vgs[vgName].pv_name
    except (KeyError, AttributeError):
        pvNames = getVG(vgName).pv_name
    return pvNames


def setrwLV(vg, lv, rw=True):
    permission = {False: 'r', True: 'rw'}[rw]
    try:
        changelv(vg, lv, ("--permission", permission))
    except se.StorageException:
        l = getLV(vg, lv)
        if l.writeable == rw:
            # Ignore the error since lv is now rw, hoping that the error was
            # because lv was already rw, see BZ#654691. We may hide here
            # another lvchange error.
            return

        raise se.CannotSetRWLogicalVolume(vg, lv, permission)


def lvsByTag(vgName, tag):
    return [lv for lv in getLV(vgName) if tag in lv.tags]


def invalidateFilter():
    _lvminfo.invalidateFilter()


# Fix me: unify with addTag
def replaceLVTag(vg, lv, deltag, addtag):
    """
    Removes and add tags atomically.
    """
    lvname = "%s/%s" % (vg, lv)
    cmd = (("lvchange",) + LVM_NOBACKUP + ("--deltag", deltag) +
           ("--addtag", addtag) + (lvname,))
    rc, out, err = _lvminfo.cmd(cmd, _lvminfo._getVGDevs((vg, )))
    _lvminfo._invalidatelvs(vg, lv)
    if rc != 0:
        raise se.LogicalVolumeReplaceTagError("%s/%s" % (vg, lv),
                                              "%s,%s" % (deltag, addtag))
