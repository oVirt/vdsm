#
# Copyright 2011-2016 Red Hat, Inc.
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

import itertools
import os
import logging
import time
import linecache
import glob
import xml.etree.ElementTree as ET
from distutils.version import LooseVersion

import libvirt

from vdsm.config import config
from vdsm import cpuarch
from vdsm import cpuinfo
from vdsm import dsaversion
from vdsm import hooks
from vdsm import hostdev
from vdsm import libvirtconnection
from vdsm import machinetype
from vdsm import netinfo
from vdsm import numa
from vdsm import host
from vdsm import utils
import storage.hba
from virt import vmdevices

# For debian systems we can use python-apt if available
try:
    import apt
    python_apt = True
except ImportError:
    python_apt = False

# For systems without rpm support
try:
    import rpm
except ImportError:
    pass

PAGE_SIZE_BYTES = os.sysconf('SC_PAGESIZE')

try:
    from gluster.api import GLUSTER_RPM_PACKAGES
    from gluster.api import GLUSTER_DEB_PACKAGES
    from gluster.api import glusterAdditionalFeatures
    _glusterEnabled = True
except ImportError:
    _glusterEnabled = False


class OSName:
    UNKNOWN = 'unknown'
    OVIRT = 'oVirt Node'
    RHEL = 'RHEL'
    FEDORA = 'Fedora'
    RHEVH = 'RHEV Hypervisor'
    DEBIAN = 'Debian'
    POWERKVM = 'PowerKVM'


RNG_SOURCES = {'random': '/dev/random',
               'hwrng': '/dev/hwrng'}


class KdumpStatus(object):
    UNKNOWN = -1
    DISABLED = 0
    ENABLED = 1


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


def _getKdumpStatus():
    try:
        # check if kdump service is running
        with open('/sys/kernel/kexec_crash_loaded', 'r') as f:
            kdumpStatus = int(f.read().strip('\n'))

        if kdumpStatus == KdumpStatus.ENABLED:
            # check if fence_kdump is configured
            kdumpStatus = KdumpStatus.DISABLED
            with open('/etc/kdump.conf', 'r') as f:
                for line in f:
                    if line.startswith('fence_kdump_nodes'):
                        kdumpStatus = KdumpStatus.ENABLED
                        break
    except (IOError, OSError, ValueError):
        kdumpStatus = KdumpStatus.UNKNOWN
        logging.debug(
            'Error detecting fence_kdump configuration status',
            exc_info=True,
        )
    return kdumpStatus


@utils.memoized
def getos():
    if os.path.exists('/etc/rhev-hypervisor-release'):
        return OSName.RHEVH
    elif glob.glob('/etc/ovirt-node-*-release'):
        return OSName.OVIRT
    elif os.path.exists('/etc/fedora-release'):
        return OSName.FEDORA
    elif os.path.exists('/etc/redhat-release'):
        return OSName.RHEL
    elif os.path.exists('/etc/debian_version'):
        return OSName.DEBIAN
    elif os.path.exists('/etc/ibm_powerkvm-release'):
        return OSName.POWERKVM
    else:
        return OSName.UNKNOWN


@utils.memoized
def osversion():
    version = release = ''

    osname = getos()
    try:
        if osname == OSName.RHEVH or osname == OSName.OVIRT:
            d = _parseKeyVal(file('/etc/default/version'))
            version = d.get('VERSION', '')
            release = d.get('RELEASE', '')
        elif osname == OSName.DEBIAN:
            version = linecache.getline('/etc/debian_version', 1).strip("\n")
            release = ""  # Debian just has a version entry
        else:
            if osname == OSName.POWERKVM:
                release_path = '/etc/ibm_powerkvm-release'
            else:
                release_path = '/etc/redhat-release'

            ts = rpm.TransactionSet()
            for er in ts.dbMatch('basenames', release_path):
                version = er['version']
                release = er['release']
    except:
        logging.error('failed to find version/release', exc_info=True)

    return dict(release=release, version=version, name=osname)


def _getSELinux():
    selinux = dict()
    selinux['mode'] = str(utils.get_selinux_enforce_mode())

    return selinux


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
                                machinetype.getCompatibleCpuModels())

    caps.update(_getVersionInfo())

    # TODO: Version requests by engine to ease handling of compatibility.
    netinfo_data = netinfo.cache.get(compatibility=30600)
    caps.update(netinfo_data)

    try:
        caps['hooks'] = hooks.installed()
    except:
        logging.debug('not reporting hooks', exc_info=True)

    caps['operatingSystem'] = osversion()
    caps['uuid'] = host.uuid()
    caps['packages2'] = _getKeyPackages()
    caps['emulatedMachines'] = machinetype.getEmulatedMachines(
        cpuarch.effective())
    caps['ISCSIInitiatorName'] = _getIscsiIniName()
    caps['HBAInventory'] = storage.hba.HBAInventory()
    caps['vmTypes'] = ['kvm']

    caps['memSize'] = str(utils.readMemInfo()['MemTotal'] / 1024)
    caps['reservedMem'] = str(config.getint('vars', 'host_mem_reserve') +
                              config.getint('vars', 'extra_mem_reserve'))
    caps['guestOverhead'] = config.get('vars', 'guest_ram_overhead')

    # Verify that our libvirt supports virtio RNG (since 10.0.2-31)
    requiredVer = LooseVersion('0.10.2-31')
    if 'libvirt' not in caps['packages2']:
        libvirtVer = None
    else:
        libvirtVer = LooseVersion(
            '-'.join((caps['packages2']['libvirt']['version'],
                      caps['packages2']['libvirt']['release'])))

    if libvirtVer is None:
        logging.debug('VirtioRNG DISABLED: unknown libvirt version')
    elif libvirtVer < requiredVer:
        logging.debug('VirtioRNG DISABLED: libvirt version %s required >= %s',
                      libvirtVer, requiredVer)
    else:
        caps['rngSources'] = vmdevices.core.Rng.available_sources()

    caps['numaNodes'] = dict(numa.topology())
    caps['numaNodeDistance'] = dict(numa.distances())
    caps['autoNumaBalancing'] = numa.autonuma_status()

    caps['selinux'] = _getSELinux()

    liveSnapSupported = _getLiveSnapshotSupport(cpuarch.effective())
    if liveSnapSupported is not None:
        caps['liveSnapshot'] = str(liveSnapSupported).lower()
    caps['liveMerge'] = str(getLiveMergeSupport()).lower()
    caps['kdumpStatus'] = _getKdumpStatus()

    caps['hostdevPassthrough'] = str(hostdev.is_supported()).lower()
    caps['additionalFeatures'] = []
    if _glusterEnabled:
        caps['additionalFeatures'].extend(glusterAdditionalFeatures())
    return caps


def _dropVersion(vstring, logMessage):
    logging.error(logMessage)

    from distutils.version import StrictVersion
    # Drop cluster supported version to be strictly less than given vstring.
    info = dsaversion.version_info.copy()
    maxVer = StrictVersion(vstring)
    info['clusterLevels'] = [ver for ver in info['clusterLevels']
                             if StrictVersion(ver) < maxVer]
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


def _getKeyPackages():
    def kernelDict():
        try:
            ret = os.uname()
            ver, rel = ret[2].split('-', 1)
        except:
            logging.error('kernel release not found', exc_info=True)
            ver, rel = '0', '0'
        try:
            t = ret[3].split()[2:]
            del t[4]  # Delete timezone
            t = time.mktime(time.strptime(' '.join(t)))
        except:
            logging.error('kernel build time not found', exc_info=True)
            t = '0'
        return dict(version=ver, release=rel, buildtime=t)

    pkgs = {'kernel': kernelDict()}

    if getos() in (OSName.RHEVH, OSName.OVIRT, OSName.FEDORA, OSName.RHEL,
                   OSName.POWERKVM):
        KEY_PACKAGES = {
            'glusterfs-cli': ('glusterfs-cli',),
            'librbd1': ('librbd1',),
            'libvirt': ('libvirt', 'libvirt-daemon-kvm'),
            'mom': ('mom',),
            'qemu-img': ('qemu-img', 'qemu-img-rhev', 'qemu-img-ev'),
            'qemu-kvm': ('qemu-kvm', 'qemu-kvm-rhev', 'qemu-kvm-ev'),
            'spice-server': ('spice-server',),
            'vdsm': ('vdsm',),
        }

        if _glusterEnabled:
            KEY_PACKAGES.update(GLUSTER_RPM_PACKAGES)

        try:
            ts = rpm.TransactionSet()

            for pkg, names in KEY_PACKAGES.iteritems():
                try:
                    mi = itertools.chain(*[ts.dbMatch('name', name)
                                           for name in names]).next()
                except StopIteration:
                    logging.debug("rpm package %s not found",
                                  KEY_PACKAGES[pkg])
                else:
                    pkgs[pkg] = {
                        'version': mi['version'],
                        'release': mi['release'],
                        'buildtime': mi['buildtime'],
                    }
        except:
            logging.error('', exc_info=True)

    elif getos() == OSName.DEBIAN and python_apt:
        KEY_PACKAGES = {
            'glusterfs-cli': 'glusterfs-cli',
            'librbd1': 'librbd1',
            'libvirt': 'libvirt0',
            'mom': 'mom',
            'qemu-img': 'qemu-utils',
            'qemu-kvm': 'qemu-kvm',
            'spice-server': 'libspice-server1',
            'vdsm': 'vdsmd',
        }

        if _glusterEnabled:
            KEY_PACKAGES.update(GLUSTER_DEB_PACKAGES)

        cache = apt.Cache()

        for pkg in KEY_PACKAGES:
            try:
                deb_pkg = KEY_PACKAGES[pkg]
                ver = cache[deb_pkg].installed.version
                # Debian just offers a version
                pkgs[pkg] = dict(version=ver, release="", buildtime="")
            except:
                logging.error('', exc_info=True)

    return pkgs
