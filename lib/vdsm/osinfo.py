#
# Copyright 2016 Red Hat, Inc.
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
import time
import os

from vdsm import utils

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
    from gluster.api import GLUSTER_RPM_PACKAGES
    from gluster.api import GLUSTER_DEB_PACKAGES
    glusterEnabled = True
except ImportError:
    glusterEnabled = False


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


def _parse_node_version(path):
    data = {}
    with open(path) as f:
        for line in f:
            try:
                key, value = [kv.strip() for kv in line.split('=', 1)]
            except ValueError:
                continue

            data[key] = value

    return data.get('VERSION', ''), data.get('RELEASE', '')


@utils.memoized
def version():
    version = release_name = ''

    osname = _release_name()
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
                version = er['version']
                release_name = er['release']
    except:
        logging.error('failed to find version/release', exc_info=True)

    return dict(release=release_name, version=version, name=osname)


def selinux_status():
    selinux = dict()
    selinux['mode'] = str(utils.get_selinux_enforce_mode())

    return selinux


def package_versions():
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

    if _release_name() in (OSName.RHEVH, OSName.OVIRT, OSName.FEDORA,
                           OSName.RHEL, OSName.POWERKVM):
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

        if glusterEnabled:
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
                pkgs[pkg] = dict(version=ver, release="", buildtime="")
            except:
                logging.error('', exc_info=True)

    return pkgs
