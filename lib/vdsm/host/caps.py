#
# Copyright 2011-2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
"""Collect host capabilities"""
from __future__ import absolute_import

import os
import logging
import xml.etree.ElementTree as ET

import libvirt

from vdsm.config import config
from vdsm.host import rngsources
from vdsm.storage import hba
from vdsm import containersconnection
from vdsm import cpuarch
from vdsm import cpuinfo
from vdsm import dsaversion
from vdsm import hooks
from vdsm import host
from vdsm import hostdev
from vdsm import hugepages
from vdsm import libvirtconnection
from vdsm import machinetype
from vdsm import numa
from vdsm import osinfo
from vdsm import supervdsm
from vdsm import utils

try:
    import ovirt_hosted_engine_ha.client.client as haClient
except ImportError:
    haClient = None


def _getFreshCapsXMLStr():
    return libvirtconnection.get().getCapabilities()


@utils.memoized
def _getCapsXMLStr():
    return _getFreshCapsXMLStr()


def _findLiveSnapshotSupport(guest):
    '''
    Returns the status of the live snapshot support
    on the hypervisor (QEMU).

    param guest:
    the `guest' XML element of the libvirt capabilities XML

    Return type: None or boolean.
    None if libvirt does not report the live
    snapshot support (as in version <= 1.2.2),
    '''
    features = guest.find('features')
    if not features:
        return None

    for feature in features.iter(tag='disksnapshot'):
        value = feature.get('default')
        if value.lower() == 'on':
            return True
        else:
            return False
    # libvirt < 1.2.2 does not export this information.
    return None


@utils.memoized
def _getLiveSnapshotSupport(arch, capabilities=None):
    if capabilities is None:
        capabilities = _getCapsXMLStr()
    caps = ET.fromstring(capabilities)

    for guestTag in caps.iter(tag='guest'):
        archTag = guestTag.find('arch')
        if archTag.get('name') == arch:
            return _findLiveSnapshotSupport(guestTag)

    return None


@utils.memoized
def getLiveMergeSupport():
    """
    Determine if libvirt provides the necessary features to enable live merge.
    We check for the existence of several libvirt flags to serve as indicators:

    VIR_DOMAIN_BLOCK_COMMIT_RELATIVE indicates that libvirt can maintain
    relative backing file path names when rewriting a backing chain.

    VIR_DOMAIN_EVENT_ID_BLOCK_JOB_2 indicates that libvirt can pass a drive
    name (ie. vda) rather than a path to the block job event callback.

    VIR_DOMAIN_BLOCK_COMMIT_ACTIVE indicates that libvirt supports merging the
    active layer using the virDomainBlockCommit API.
    """
    for flag in ('VIR_DOMAIN_BLOCK_COMMIT_RELATIVE',
                 'VIR_DOMAIN_EVENT_ID_BLOCK_JOB_2',
                 'VIR_DOMAIN_BLOCK_COMMIT_ACTIVE'):
        if not hasattr(libvirt, flag):
            logging.debug("libvirt is missing '%s': live merge disabled", flag)
            return False
    return True


def _parseKeyVal(lines, delim='='):
    d = {}
    for line in lines:
        kv = line.split(delim, 1)
        if len(kv) != 2:
            continue
        k, v = map(str.strip, kv)
        d[k] = v
    return d


def _getIscsiIniName():
    try:
        with open('/etc/iscsi/initiatorname.iscsi') as f:
            return _parseKeyVal(f)['InitiatorName']
    except:
        logging.error('reporting empty InitiatorName', exc_info=True)
    return ''


def get():
    caps = {}
    cpu_topology = numa.cpu_topology()

    caps['kvmEnabled'] = str(os.path.exists('/dev/kvm')).lower()

    if config.getboolean('vars', 'report_host_threads_as_cores'):
        caps['cpuCores'] = str(cpu_topology.threads)
    else:
        caps['cpuCores'] = str(cpu_topology.cores)

    caps['cpuThreads'] = str(cpu_topology.threads)
    caps['cpuSockets'] = str(cpu_topology.sockets)
    caps['onlineCpus'] = ','.join(cpu_topology.online_cpus)
    caps['cpuSpeed'] = cpuinfo.frequency()
    caps['cpuModel'] = cpuinfo.model()
    caps['cpuFlags'] = ','.join(cpuinfo.flags() +
                                machinetype.compatible_cpu_models())

    caps.update(_getVersionInfo())

    net_caps = supervdsm.getProxy().network_caps()
    caps.update(net_caps)

    try:
        caps['hooks'] = hooks.installed()
    except:
        logging.debug('not reporting hooks', exc_info=True)

    caps['operatingSystem'] = osinfo.version()
    caps['uuid'] = host.uuid()
    caps['packages2'] = osinfo.package_versions()
    caps['realtimeKernel'] = osinfo.runtime_kernel_flags().realtime
    caps['kernelArgs'] = osinfo.kernel_args()
    caps['nestedVirtualization'] = osinfo.nested_virtualization().enabled
    caps['emulatedMachines'] = machinetype.emulated_machines(
        cpuarch.effective())
    caps['ISCSIInitiatorName'] = _getIscsiIniName()
    caps['HBAInventory'] = hba.HBAInventory()
    caps['vmTypes'] = ['kvm']

    caps['memSize'] = str(utils.readMemInfo()['MemTotal'] / 1024)
    caps['reservedMem'] = str(config.getint('vars', 'host_mem_reserve') +
                              config.getint('vars', 'extra_mem_reserve'))
    caps['guestOverhead'] = config.get('vars', 'guest_ram_overhead')

    caps['rngSources'] = rngsources.list_available()

    caps['numaNodes'] = dict(numa.topology())
    caps['numaNodeDistance'] = dict(numa.distances())
    caps['autoNumaBalancing'] = numa.autonuma_status()

    caps['selinux'] = osinfo.selinux_status()

    liveSnapSupported = _getLiveSnapshotSupport(cpuarch.effective())
    if liveSnapSupported is not None:
        caps['liveSnapshot'] = str(liveSnapSupported).lower()
    caps['liveMerge'] = str(getLiveMergeSupport()).lower()
    caps['kdumpStatus'] = osinfo.kdump_status()

    caps['hostdevPassthrough'] = str(hostdev.is_supported()).lower()
    caps['additionalFeatures'] = []
    if osinfo.glusterEnabled:
        from vdsm.gluster.api import glusterAdditionalFeatures
        caps['additionalFeatures'].extend(glusterAdditionalFeatures())
    caps['containers'] = containersconnection.is_supported()
    caps['hostedEngineDeployed'] = _isHostedEngineDeployed()
    caps['hugepages'] = hugepages.supported()
    return caps


def _dropVersion(vstring, logMessage):
    logging.error(logMessage)

    from distutils import version
    # Drop cluster supported version to be strictly less than given vstring.
    info = dsaversion.version_info.copy()
    maxVer = version.StrictVersion(vstring)
    info['clusterLevels'] = [ver for ver in info['clusterLevels']
                             if version.StrictVersion(ver) < maxVer]
    return info


@utils.memoized
def _getVersionInfo():
    if not hasattr(libvirt, 'VIR_MIGRATE_ABORT_ON_ERROR'):
        return _dropVersion('3.4',
                            'VIR_MIGRATE_ABORT_ON_ERROR not found in libvirt,'
                            ' support for clusterLevel >= 3.4 is disabled.'
                            ' For Fedora 19 users, please consider upgrading'
                            ' libvirt from the virt-preview repository')

    if not hasattr(libvirt, 'VIR_MIGRATE_AUTO_CONVERGE'):
        return _dropVersion('3.6',
                            'VIR_MIGRATE_AUTO_CONVERGE not found in libvirt,'
                            ' support for clusterLevel >= 3.6 is disabled.'
                            ' For Fedora 20 users, please consider upgrading'
                            ' libvirt from the virt-preview repository')

    if not hasattr(libvirt, 'VIR_MIGRATE_COMPRESSED'):
        return _dropVersion('3.6',
                            'VIR_MIGRATE_COMPRESSED not found in libvirt,'
                            ' support for clusterLevel >= 3.6 is disabled.'
                            ' For Fedora 20 users, please consider upgrading'
                            ' libvirt from the virt-preview repository')

    return dsaversion.version_info


def _isHostedEngineDeployed():
    if not haClient:
        return False

    client = haClient.HAClient()
    try:
        is_deployed = client.is_deployed
    except AttributeError:
        logging.warning("The installed version of hosted engine doesn't "
                        "support the checking of deployment status.")
        return False

    return is_deployed()
