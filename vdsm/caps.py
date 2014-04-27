#
# Copyright 2011 Red Hat, Inc.
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
import platform
from xml.dom import minidom
import logging
import time
import linecache
import glob
import re

import libvirt
import rpm

from vdsm.config import config
from vdsm import libvirtconnection
import dsaversion
from vdsm import netinfo
import hooks
from vdsm import utils
import storage.hba

# For debian systems we can use python-apt if available
try:
    import apt
    python_apt = True
except ImportError:
    python_apt = False


try:
    from gluster.api import GLUSTER_RPM_PACKAGES
    from gluster.api import GLUSTER_DEB_PACKAGES
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


class AutoNumaBalancingStatus:
    DISABLE = 0
    ENABLE = 1
    UNKNOWN = 2


RNG_SOURCES = {'random': '/dev/random',
               'hwrng': '/dev/hwrng'}


class Architecture:
    X86_64 = 'x86_64'
    PPC64 = 'ppc64'


class CpuInfo(object):
    def __init__(self, cpuinfo='/proc/cpuinfo'):
        """Parse /proc/cpuinfo"""
        self._info = {}
        p = {}
        self._arch = platform.machine()

        for line in file(cpuinfo):
            if line.strip() == '':
                p = {}
                continue
            key, value = map(str.strip, line.split(':', 1))
            if key == 'processor':
                self._info[value] = p
            else:
                p[key] = value

    def flags(self):
        if self._arch == Architecture.X86_64:
            return self._info.itervalues().next()['flags'].split()
        elif self._arch == Architecture.PPC64:
            return ['powernv']
        else:
            raise RuntimeError('Unsupported architecture')

    def mhz(self):
        if self._arch == Architecture.X86_64:
            return self._info.itervalues().next()['cpu MHz']
        elif self._arch == Architecture.PPC64:
            clock = self._info.itervalues().next()['clock']
            return clock[:-3]
        else:
            raise RuntimeError('Unsupported architecture')

    def model(self):
        if self._arch == Architecture.X86_64:
            return self._info.itervalues().next()['model name']
        elif self._arch == Architecture.PPC64:
            return self._info.itervalues().next()['cpu']
        else:
            raise RuntimeError('Unsupported architecture')


class CpuTopology(object):
    def __init__(self, capabilities=None):

        if platform.machine() == Architecture.PPC64:
            from ppc64HardwareInfo import \
                getCpuTopology as getPPC64CpuTopology
            self._topology = getPPC64CpuTopology(capabilities)
        else:
            self._topology = _getCpuTopology(capabilities)

    def threads(self):
        return self._topology['threads']

    def cores(self):
        return self._topology['cores']

    def sockets(self):
        return self._topology['sockets']


@utils.memoized
def _getCapsXMLStr():
    return libvirtconnection.get().getCapabilities()


@utils.memoized
def _getCpuTopology(capabilities):
    if capabilities is None:
        capabilities = _getCapsXMLStr()

    caps = minidom.parseString(capabilities)
    host = caps.getElementsByTagName('host')[0]
    cells = host.getElementsByTagName('cells')[0]
    cpus = cells.getElementsByTagName('cpu').length

    sockets = set()
    siblings = set()
    for cpu in cells.getElementsByTagName('cpu'):
        sockets.add(cpu.getAttribute('socket_id'))
        siblings.add(cpu.getAttribute('siblings'))

    topology = {'sockets': len(sockets),
                'cores': len(siblings),
                'threads': cpus}

    return topology


@utils.memoized
def _getNumaTopology():
    capabilities = _getCapsXMLStr()
    caps = minidom.parseString(capabilities)
    host = caps.getElementsByTagName('host')[0]
    cells = host.getElementsByTagName('cells')[0]
    cellsInfo = {}
    cellSets = cells.getElementsByTagName('cell')
    for cell in cellSets:
        cellInfo = {}
        cpus = []
        for cpu in cell.getElementsByTagName('cpu'):
            cpus.append(int(cpu.getAttribute('id')))
        cellInfo['cpus'] = cpus
        cellIndex = cell.getAttribute('id')
        if cellSets.length < 2:
            memInfo = _getUMAHostMemoryStats()
        else:
            memInfo = _getMemoryStatsByNumaCell(int(cellIndex))
        cellInfo['totalMemory'] = memInfo['total']
        cellsInfo[cellIndex] = cellInfo
    return cellsInfo


def _getMemoryStatsByNumaCell(cell):
    """
    Get the memory stats of a specified numa node, the unit is MiB.

    :param cell: the index of numa node
    :type cell: int
    :return: dict like {'total': '49141', 'free': '46783'}
    """
    cellMemInfo = libvirtconnection.get().getMemoryStats(cell, 0)
    cellMemInfo['total'] = str(cellMemInfo['total'] / 1024)
    cellMemInfo['free'] = str(cellMemInfo['free'] / 1024)
    return cellMemInfo


def _getUMAHostMemoryStats():
    """
    Get the memory stats of a UMA host, the unit is MiB.

    :return: dict like {'total': '49141', 'free': '46783'}
    """
    memDict = {}
    memInfo = utils.readMemInfo()
    memDict['total'] = str(memInfo['MemTotal'] / 1024)
    memDict['free'] = str(memInfo['MemFree'] / 1024)
    return memDict


@utils.memoized
def _getNumaNodeDistance():
    nodeDistance = {}
    retcode, out, err = utils.execCmd(['numactl', '--hardware'])
    if retcode != 0:
        logging.error("Get error when execute numactl", exc_info=True)
        return nodeDistance
    pattern = re.compile(r'\s+(\d+):(.*)')
    for item in out:
        match = pattern.match(item)
        if match:
            nodeDistance[match.group(1)] = map(int,
                                               match.group(2).strip().split())
    return nodeDistance


@utils.memoized
def _getAutoNumaBalancingInfo():
    retcode, out, err = utils.execCmd(['sysctl', '-n', '-e',
                                       'kernel.numa_balancing'])
    if not out:
        return AutoNumaBalancingStatus.UNKNOWN
    elif out[0] == '0':
        return AutoNumaBalancingStatus.DISABLE
    elif out[0] == '1':
        return AutoNumaBalancingStatus.ENABLE
    else:
        return AutoNumaBalancingStatus.UNKNOWN


@utils.memoized
def _getEmulatedMachines(arch, capabilities=None):
    if capabilities is None:
        capabilities = _getCapsXMLStr()
    caps = minidom.parseString(capabilities)

    for archTag in caps.getElementsByTagName('arch'):
        if archTag.getAttribute('name') == arch:
            return [m.firstChild.data for m in archTag.childNodes
                    if m.nodeName == 'machine']
    return []


def _getAllCpuModels():
    cpu_map = minidom.parseString(
        file('/usr/share/libvirt/cpu_map.xml').read())

    arch = platform.machine()

    # In libvirt CPU map XML, both x86_64 and x86 are
    # the same architecture, so in order to find all
    # the CPU models for this architecture, 'x86'
    # must be used
    if arch == Architecture.X86_64:
        arch = 'x86'

    architectureElement = None

    architectureElements = cpu_map.getElementsByTagName('arch')

    if architectureElements:
        for a in architectureElements:
            if a.getAttribute('name') == arch:
                architectureElement = a

    if architectureElement is None:
        logging.error('Error while getting all CPU models: the host '
                      'architecture is not supported', exc_info=True)
        return {}

    allModels = dict()

    for m in architectureElement.childNodes:
        if m.nodeName != 'model':
            continue
        element = m.getElementsByTagName('vendor')
        if element:
            vendor = element[0].getAttribute('name')
        else:
            # If current model doesn't have a vendor, check if it has a model
            # that it is based on. The models in the cpu_map.xml file are
            # sorted in a way that the base model is always defined before.
            element = m.getElementsByTagName('model')
            if element:
                vendor = allModels.get(element[0].getAttribute('name'), None)
            else:
                vendor = None
        allModels[m.getAttribute('name')] = vendor

    return allModels


@utils.memoized
def _getCompatibleCpuModels():
    c = libvirtconnection.get()
    allModels = _getAllCpuModels()

    def compatible(model, vendor):
        if not vendor:
            return False
        xml = '<cpu match="minimum"><model>%s</model>' \
              '<vendor>%s</vendor></cpu>' % (model, vendor)
        try:
            return c.compareCPU(xml, 0) in (libvirt.VIR_CPU_COMPARE_SUPERSET,
                                            libvirt.VIR_CPU_COMPARE_IDENTICAL)
        except libvirt.libvirtError as e:
            # hack around libvirt BZ#795836
            if e.get_error_code() == libvirt.VIR_ERR_OPERATION_INVALID:
                return False
            raise

    return ['model_' + model for (model, vendor)
            in allModels.iteritems() if compatible(model, vendor)]


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
        return _parseKeyVal(
            file('/etc/iscsi/initiatorname.iscsi'))['InitiatorName']
    except:
        logging.error('reporting empty InitiatorName', exc_info=True)
    return ''


def _getRngSources():
    return [source for (source, path) in RNG_SOURCES.items()
            if os.path.exists(path)]


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
            ts = rpm.TransactionSet()
            for er in ts.dbMatch('basenames', '/etc/redhat-release'):
                version = er['version']
                release = er['release']
    except:
        logging.error('failed to find version/release', exc_info=True)

    return dict(release=release, version=version, name=osname)


def getTargetArch():
    if config.getboolean('vars', 'fake_kvm_support'):
        return config.get('vars', 'fake_kvm_architecture')
    else:
        return platform.machine()


def _getSELinuxEnforceMode():
    """
    Returns the SELinux mode as reported by kernel.

    1 = enforcing - SELinux security policy is enforced.
    0 = permissive - SELinux prints warnings instead of enforcing.
    -1 = disabled - No SELinux policy is loaded.
    """
    selinux_mnts = ['/sys/fs/selinux', '/selinux']
    for mnt in selinux_mnts:
        enforce_path = os.path.join(mnt, 'enforce')
        if not os.path.exists(enforce_path):
            continue

        with open(enforce_path) as fileStream:
            return int(fileStream.read().strip())

    # Assume disabled if cannot find
    return -1


def _getSELinux():
    selinux = dict()
    selinux['mode'] = str(_getSELinuxEnforceMode())

    return selinux


def get():
    targetArch = getTargetArch()

    caps = {}

    caps['kvmEnabled'] = \
        str(config.getboolean('vars', 'fake_kvm_support') or
            os.path.exists('/dev/kvm')).lower()

    cpuInfo = CpuInfo()
    cpuTopology = CpuTopology()
    if config.getboolean('vars', 'report_host_threads_as_cores'):
        caps['cpuCores'] = str(cpuTopology.threads())
    else:
        caps['cpuCores'] = str(cpuTopology.cores())

    caps['cpuThreads'] = str(cpuTopology.threads())
    caps['cpuSockets'] = str(cpuTopology.sockets())
    caps['cpuSpeed'] = cpuInfo.mhz()
    if config.getboolean('vars', 'fake_kvm_support'):
        if targetArch == Architecture.X86_64:
            caps['cpuModel'] = 'Intel(Fake) CPU'

            flagList = ['vmx', 'sse2', 'nx']

            if targetArch == platform.machine():
                flagList += cpuInfo.flags()

            flags = set(flagList)

            caps['cpuFlags'] = ','.join(flags) + ',model_486,model_pentium,' \
                'model_pentium2,model_pentium3,model_pentiumpro,' \
                'model_qemu32,model_coreduo,model_core2duo,model_n270,' \
                'model_Conroe,model_Penryn,model_Nehalem,model_Opteron_G1'
        elif targetArch == Architecture.PPC64:
            caps['cpuModel'] = 'POWER 7 (fake)'
            caps['cpuFlags'] = 'powernv,model_POWER7_v2.3'
        else:
            raise RuntimeError('Unsupported architecture: %s' % targetArch)
    else:
        caps['cpuModel'] = cpuInfo.model()
        caps['cpuFlags'] = ','.join(cpuInfo.flags() +
                                    _getCompatibleCpuModels())

    caps.update(_getVersionInfo())
    caps.update(netinfo.get())

    try:
        caps['hooks'] = hooks.installed()
    except:
        logging.debug('not reporting hooks', exc_info=True)

    caps['operatingSystem'] = osversion()
    caps['uuid'] = utils.getHostUUID()
    caps['packages2'] = _getKeyPackages()
    caps['emulatedMachines'] = _getEmulatedMachines(targetArch)
    caps['ISCSIInitiatorName'] = _getIscsiIniName()
    caps['HBAInventory'] = storage.hba.HBAInventory()
    caps['vmTypes'] = ['kvm']

    caps['memSize'] = str(utils.readMemInfo()['MemTotal'] / 1024)
    caps['reservedMem'] = str(config.getint('vars', 'host_mem_reserve') +
                              config.getint('vars', 'extra_mem_reserve'))
    caps['guestOverhead'] = config.get('vars', 'guest_ram_overhead')
    caps['rngSources'] = _getRngSources()
    caps['numaNodes'] = _getNumaTopology()
    caps['numaNodeDistance'] = _getNumaNodeDistance()
    caps['autoNumaBalancing'] = _getAutoNumaBalancingInfo()

    caps['selinux'] = _getSELinux()

    return caps


@utils.memoized
def _getVersionInfo():
    # commit bbeb165e42673cddc87495c3d12c4a7f7572013c
    # added default abort of the VM migration on EIO.
    # libvirt 1.0.5.8 found in Fedora 19 does not export
    # that flag, even though it should be present since 1.0.1.
    if hasattr(libvirt, 'VIR_MIGRATE_ABORT_ON_ERROR'):
        return dsaversion.version_info

    logging.error('VIR_MIGRATE_ABORT_ON_ERROR not found in libvirt,'
                  ' support for clusterLevel >= 3.4 is disabled.'
                  ' For Fedora 19 users, please consider upgrading'
                  ' libvirt from the virt-preview repository')

    from distutils.version import StrictVersion
    # Workaround: we drop the cluster 3.4+
    # compatibility when we run on top of
    # a libvirt without this flag.
    info = dsaversion.version_info.copy()
    maxVer = StrictVersion('3.4')
    info['clusterLevels'] = [ver for ver in info['clusterLevels']
                             if StrictVersion(ver) < maxVer]
    return info


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

    if getos() in (OSName.RHEVH, OSName.OVIRT, OSName.FEDORA, OSName.RHEL):
        KEY_PACKAGES = {'qemu-kvm': ('qemu-kvm', 'qemu-kvm-rhev'),
                        'qemu-img': ('qemu-img', 'qemu-img-rhev'),
                        'vdsm': ('vdsm',),
                        'spice-server': ('spice-server',),
                        'libvirt': ('libvirt', 'libvirt-daemon-kvm'),
                        'mom': ('mom',),
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
        KEY_PACKAGES = {'qemu-kvm': 'qemu-kvm', 'qemu-img': 'qemu-utils',
                        'vdsm': 'vdsmd', 'spice-server': 'libspice-server1',
                        'libvirt': 'libvirt0', 'mom': 'mom'}

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


def isOvirtNode():
    return getos() in (OSName.RHEVH, OSName.OVIRT)
