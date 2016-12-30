#
# Copyright 2016-2017 Red Hat, Inc.
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
from __future__ import absolute_import

import itertools
import glob
import linecache
import logging
import os

from collections import namedtuple

from vdsm import cpuarch
from vdsm import utils
from vdsm.common import cache

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

try:
    from vdsm.gluster.api import GLUSTER_RPM_PACKAGES
    from vdsm.gluster.api import GLUSTER_DEB_PACKAGES
    glusterEnabled = True
except ImportError:
    glusterEnabled = False


KernelFlags = namedtuple('KernelFlags', 'version, realtime')
NestedVirtualization = namedtuple('NestedVirtualization',
                                  'enabled, kvm_module')


class OSName:
    UNKNOWN = 'unknown'
    OVIRT = 'oVirt Node'
    RHEL = 'RHEL'
    FEDORA = 'Fedora'
    RHEVH = 'RHEV Hypervisor'
    DEBIAN = 'Debian'
    POWERKVM = 'PowerKVM'


class KdumpStatus(object):
    UNKNOWN = -1
    DISABLED = 0
    ENABLED = 1


def kdump_status():
    try:
        # check if kdump service is running
        with open('/sys/kernel/kexec_crash_loaded', 'r') as f:
            status = int(f.read().strip('\n'))

        if status == KdumpStatus.ENABLED:
            # check if fence_kdump is configured
            status = KdumpStatus.DISABLED
            with open('/etc/kdump.conf', 'r') as f:
                for line in f:
                    if line.startswith('fence_kdump_nodes'):
                        status = KdumpStatus.ENABLED
                        break
    except (IOError, OSError, ValueError):
        status = KdumpStatus.UNKNOWN
        logging.debug(
            'Error detecting fence_kdump configuration status',
            exc_info=True,
        )
    return status


@cache.memoized
def _release_name():
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


def _parse_release_file(path):
    data = {}
    try:
        with open(path) as f:
            for line in f:
                try:
                    key, value = [kv.strip() for kv in line.split('=', 1)]
                except ValueError:
                    continue

                data[key] = value
    except IOError:
        logging.exception('Fail to read release file')
    return data


def _parse_node_version(path):
    data = _parse_release_file(path)
    return data.get('VERSION', ''), data.get('RELEASE', '')


def _next_gen_node():
    """
    Return:
        True if it's oVirt Node Next or RHV Node
    """
    ts = rpm.TransactionSet()
    for pkg in ts.dbMatch():
        if (pkg['name'] == 'redhat-release-virtualization-host' or
                pkg['name'] == 'ovirt-release-host-node'):
            return True

    return False


@cache.memoized
def _get_os_release_data(var_name):
    """
    The /etc/os-release file contain operating
    system identification data.

    Param:
          var_name is the variable name of /etc/os-release

    Return:
          return the value found or ""
    """
    var_value = ''
    _os_release_file = '/etc/os-release'

    if os.path.exists(_os_release_file):
        data = _parse_release_file(_os_release_file)
        if data.get(var_name) is not None:
            var_value = data.get(var_name).strip('"')
    return var_value


def _get_pretty_name():
    return _get_os_release_data('PRETTY_NAME')


def _get_version_id():
    return _get_os_release_data('VERSION_ID')


@cache.memoized
def kernel_args(path='/proc/cmdline'):
    with open(path, 'r') as f:
        return f.readline().strip()


@cache.memoized
def kernel_args_dict(path='/proc/cmdline'):
    cmdline = kernel_args().split(' ')

    # This is poor and limited, but sufficient for key=value portion of
    # cmdline.
    ret = {}
    for option in cmdline:
        try:
            key, value = option.split('=')
        except ValueError:
            continue

        ret[key] = value

    return ret


@cache.memoized
def version():
    version = release_name = ''

    osname = _release_name()
    pretty_name = _get_pretty_name()
    try:
        if osname == OSName.RHEVH or osname == OSName.OVIRT:
            version, release_name = _parse_node_version('/etc/default/version')
        elif osname == OSName.DEBIAN:
            version = linecache.getline('/etc/debian_version', 1).strip("\n")
            release_name = ""  # Debian just has a version entry
        else:
            if osname == OSName.POWERKVM:
                release_path = '/etc/ibm_powerkvm-release'
            else:
                release_path = '/etc/redhat-release'

            ts = rpm.TransactionSet()
            for er in ts.dbMatch('basenames', release_path):

                if _next_gen_node():
                    version = _get_version_id()
                    release_name = er['release']
                else:
                    version = er['version']
                    release_name = er['release']
    except:
        logging.error('failed to find version/release', exc_info=True)

    return dict(release=release_name, version=version,
                name=osname, pretty_name=pretty_name)


def selinux_status():
    selinux = dict()
    selinux['mode'] = str(utils.get_selinux_enforce_mode())

    return selinux


def package_versions():
    pkgs = {'kernel': runtime_kernel_flags().version}

    if _release_name() in (OSName.RHEVH, OSName.OVIRT, OSName.FEDORA,
                           OSName.RHEL, OSName.POWERKVM):
        KEY_PACKAGES = {
            'glusterfs-cli': ('glusterfs-cli',),
            'librbd1': ('librbd1',),
            'libvirt': ('libvirt', 'libvirt-daemon-kvm'),
            'mom': ('mom',),
            'ovirt-hosted-engine-ha': ('ovirt-hosted-engine-ha',),
            'qemu-img': ('qemu-img', 'qemu-img-rhev', 'qemu-img-ev'),
            'qemu-kvm': ('qemu-kvm', 'qemu-kvm-rhev', 'qemu-kvm-ev'),
            'spice-server': ('spice-server',),
            'vdsm': ('vdsm',),
        }

        if glusterEnabled:
            KEY_PACKAGES.update(GLUSTER_RPM_PACKAGES)

        try:
            ts = rpm.TransactionSet()

            for pkg, names in KEY_PACKAGES.iteritems():
                try:
                    mi = next(itertools.chain(
                        *[ts.dbMatch('name', name) for name in names]))
                except StopIteration:
                    logging.debug("rpm package %s not found",
                                  KEY_PACKAGES[pkg])
                else:
                    pkgs[pkg] = {
                        'version': mi['version'],
                        'release': mi['release'],
                    }
        except Exception:
            logging.error('', exc_info=True)

    elif _release_name() == OSName.DEBIAN and python_apt:
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

        if glusterEnabled:
            KEY_PACKAGES.update(GLUSTER_DEB_PACKAGES)

        cache = apt.Cache()

        for pkg in KEY_PACKAGES:
            try:
                deb_pkg = KEY_PACKAGES[pkg]
                ver = cache[deb_pkg].installed.version
                # Debian just offers a version
                pkgs[pkg] = dict(version=ver, release="")
            except Exception:
                logging.error('', exc_info=True)

    return pkgs


@cache.memoized
def runtime_kernel_flags():
    ret = os.uname()
    try:
        ver, rel = ret[2].split('-', 1)
    except ValueError:
        logging.error('kernel release not found', exc_info=True)
        ver, rel = '0', '0'

    realtime = 'RT' in ret[3]

    return KernelFlags(dict(version=ver, release=rel), realtime)


@cache.memoized
def nested_virtualization():
    if cpuarch.is_ppc(cpuarch.real()):
        return NestedVirtualization(False, None)

    for kvm_module in ("kvm_intel", "kvm_amd"):
        kvm_module_path = "/sys/module/%s/parameters/nested" % kvm_module
        try:
            with open(kvm_module_path) as f:
                if f.readline().strip() in ("Y", "1"):
                    return NestedVirtualization(True, kvm_module)
        except IOError:
            logging.debug('%s nested virtualization '
                          'not detected' % kvm_module, exc_info=True)

    logging.debug('Could not determine status of nested '
                  'virtualization')
    return NestedVirtualization(False, None)
