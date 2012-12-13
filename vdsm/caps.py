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

import os
from xml.dom import minidom
import logging
import time
import struct
import socket
import itertools
import linecache
import glob

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


class OSName:
    UNKNOWN = 'unknown'
    OVIRT = 'oVirt Node'
    RHEL = 'RHEL'
    FEDORA = 'Fedora'
    RHEVH = 'RHEV Hypervisor'
    DEBIAN = 'Debian'


class CpuInfo(object):
    def __init__(self, cpuinfo='/proc/cpuinfo'):
        """Parse /proc/cpuinfo"""
        self._info = {}
        p = {}
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
        return self._info.itervalues().next()['flags'].split()

    def mhz(self):
        return self._info.itervalues().next()['cpu MHz']

    def model(self):
        return self._info.itervalues().next()['model name']


class CpuTopology(object):
    def __init__(self, capabilities=None):
        self._topology = _getCpuTopology(capabilities)

    def threads(self):
        return (self._topology['threads'])

 # this assumes that all numa nodes have the same number of sockets,
 # and that all socket have the same number of cores.
    def cores(self):
        return (self._topology['cells'] *
                self._topology['sockets'] *
                self._topology['cores'])

    def sockets(self):
        return (self._topology['cells'] *
                self._topology['sockets'])


@utils.memoized
def _getCapsXMLStr():
    return libvirtconnection.get().getCapabilities()


@utils.memoized
def _getCpuTopology(capabilities):
    if capabilities is None:
        capabilities = _getCapsXMLStr()
    caps = minidom.parseString(capabilities)
    host = caps.getElementsByTagName('host')[0]
    cpu = host.getElementsByTagName('cpu')[0]
    cells = host.getElementsByTagName('cells')[0]
    topology = {'cells': int(cells.getAttribute('num')),
                'sockets': int(cpu.getElementsByTagName('topology')[0].
                               getAttribute('sockets')),
                'cores': int(cpu.getElementsByTagName('topology')[0].
                             getAttribute('cores')),
                'threads': cells.getElementsByTagName('cpu').length}
    return topology


@utils.memoized
def _getEmulatedMachines(capabilities=None):
    if capabilities is None:
        capabilities = _getCapsXMLStr()
    caps = minidom.parseString(capabilities)
    for archTag in caps.getElementsByTagName('arch'):
        if archTag.getAttribute('name') == 'x86_64':
            return [m.firstChild.data for m in archTag.childNodes
                    if m.nodeName == 'machine']
    return []


def _getAllCpuModels():
    cpu_map = minidom.parseString(
        file('/usr/share/libvirt/cpu_map.xml').read())

    allModels = dict()
    for m in cpu_map.getElementsByTagName('arch')[0].childNodes:
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
        except libvirt.libvirtError, e:
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


def get():
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

    caps['cpuSockets'] = str(cpuTopology.sockets())
    caps['cpuSpeed'] = cpuInfo.mhz()
    if config.getboolean('vars', 'fake_kvm_support'):
        caps['cpuModel'] = 'Intel(Fake) CPU'
        flags = set(cpuInfo.flags() + ['vmx', 'sse2', 'nx'])
        caps['cpuFlags'] = ','.join(flags) + 'model_486,model_pentium,' \
            'model_pentium2,model_pentium3,model_pentiumpro,model_qemu32,' \
            'model_coreduo,model_core2duo,model_n270,model_Conroe,' \
            'model_Penryn,model_Nehalem,model_Opteron_G1'
    else:
        caps['cpuModel'] = cpuInfo.model()
        caps['cpuFlags'] = ','.join(cpuInfo.flags() +
                                    _getCompatibleCpuModels())

    caps.update(dsaversion.version_info)
    caps.update(netinfo.get())

    try:
        caps['hooks'] = hooks.installed()
    except:
        logging.debug('not reporting hooks', exc_info=True)

    caps['operatingSystem'] = osversion()
    caps['uuid'] = utils.getHostUUID()
    caps['packages2'] = _getKeyPackages()
    caps['emulatedMachines'] = _getEmulatedMachines()
    caps['ISCSIInitiatorName'] = _getIscsiIniName()
    caps['HBAInventory'] = storage.hba.HBAInventory()
    caps['vmTypes'] = ['kvm']

    caps['memSize'] = str(utils.readMemInfo()['MemTotal'] / 1024)
    caps['reservedMem'] = str(config.getint('vars', 'host_mem_reserve') +
                              config.getint('vars', 'extra_mem_reserve'))
    caps['guestOverhead'] = config.get('vars', 'guest_ram_overhead')

    return caps


def _getIfaceByIP(addr, fileName='/proc/net/route'):
    remote = struct.unpack('I', socket.inet_aton(addr))[0]
    for line in itertools.islice(file(fileName), 1, None):
        (iface, dest, gateway, flags, refcnt, use, metric,
         mask, mtu, window, irtt) = line.split()
        dest = int(dest, 16)
        mask = int(mask, 16)
        if remote & mask == dest & mask:
            return iface
    return ''  # should never get here w/ default gw


def _getKeyPackages():
    def kernelDict():
        try:
            with open('/proc/sys/kernel/osrelease', "r") as f:
                ver, rel = f.read().strip().split('-', 1)
        except:
            logging.error('kernel release not found', exc_info=True)
            ver, rel = '0', '0'
        try:
            t = file('/proc/sys/kernel/version').read().split()[2:]
            del t[4]  # Delete timezone
            t = time.mktime(time.strptime(' '.join(t)))
        except:
            logging.error('kernel build time not found', exc_info=True)
            t = '0'
        return dict(version=ver, release=rel, buildtime=t)

    pkgs = {'kernel': kernelDict()}

    if getos() in (OSName.RHEVH, OSName.OVIRT, OSName.FEDORA, OSName.RHEL):
        KEY_PACKAGES = ['qemu-kvm', 'qemu-img',
                        'vdsm', 'spice-server', 'libvirt', 'mom']

        try:
            ts = rpm.TransactionSet()

            for pkg in KEY_PACKAGES:
                try:
                    mi = ts.dbMatch('name', pkg).next()
                except StopIteration:
                    logging.debug("rpm package %s not found", pkg)
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
