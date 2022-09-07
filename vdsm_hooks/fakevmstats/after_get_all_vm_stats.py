#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

"""
To Enable this set fake_vmstats_enable=true in /etv/vdsm/vdsm.conf.
To set this automatically via ovirt-host-deploy

/etc/ovirt-host-deploy.conf.d/50-fake-stats.conf
    VDSM/configOverride=bool:False
    VDSM_CONFIG/fake_vmstats_enable=str:true
"""
import hooking
import codecs
import random

from vdsm.common.units import MiB, GiB
from vdsm.config import config

QUARTER_GB = 256 * MiB

# Config
MAX_DYNAMIC_MOUNTS = 1
APP_LIST = """
libXfixes-5.0-3.el6.i686
python-decorator-3.0.1-3.1.el6.noarch
filesystem-2.4.30-3.el6.x86_64
libXrandr-1.4.0-1.el6.i686
redhat-support-lib-python-0.9.5-9.el6.noarch
libX11-common-1.5.0-4.el6.noarch
libXi-1.6.1-3.el6.i686
python-toscawidgets-0.9.8-1.el6.noarch
glibc-2.12-1.132.el6.x86_64
python-formencode-1.2.2-2.1.el6.noarch
libcap-2.16-5.5.el6.x86_64
libgcrypt-1.4.5-11.el6_4.i686
libcom_err-1.41.12-18.el6.x86_64
ncurses-libs-5.7-3.20090208.el6.i686
libuser-python-0.56.13-5.el6.x86_64
pygtk2-2.16.0-3.el6.x86_64
nspluginwrapper-1.4.4-1.el6_3.i686
python-myghty-1.1-11.el6.noarch
libuuid-2.17.2-12.14.el6.x86_64
nss-util-3.15.3-1.el6_5.x86_64
python-transaction-1.0.1-1.el6.noarch
sed-4.2.1-10.el6.x86_64
openldap-2.4.23-34.el6_5.1.x86_64
python-repoze-what-1.0.8-6.el6.noarch
findutils-4.4.2-6.el6.x86_64
libvirt-client-0.10.2-29.el6_5.5.x86_64
p11-kit-trust-0.18.5-2.el6_5.2.x86_64
python-markdown-2.0.1-3.1.el6.noarch
gawk-3.1.7-10.el6.x86_64
python-repoze-what-pylons-1.0-4.el6.noarch
libtalloc-2.0.7-2.el6.x86_64
gnutls-utils-2.8.5-13.el6_5.x86_64
python-prioritized-methods-0.2.1-5.1.el6.noarch""".split()


def createDiskUsage(path, fs, total=None, used=None):
    if total is None:
        total = random.randint(8, 400) * QUARTER_GB
    if used is None:
        used = random.randint(QUARTER_GB, total)
    return {'path': path,
            'fs': fs,
            'total': str(total),
            'used': str(used)}


def uuidDigest(uuid):
    """
    Extract len bytes of data from the provided uuid. Used to have 'persistent'
    value for each vm but different across different VMs.

    - Returns values 0 to 255
    - Supposed to be simple and relatively fast.
    """

    bytes_string = codecs.decode(uuid.replace('-', ''), 'hex')
    bytes_list = [ord(x) for x in bytes_string]
    idx = 0
    iteration = 0
    while True:
        yield (bytes_list[idx] + iteration) % 256

        idx += 1
        if idx == len(bytes_list):
            idx = 0
            iteration += 1


ETH_HW_ADDR_FORMAT = '%02x:%02x:%02x:%02x:%02x:%02x'


def randomizeRuntimeStats(stats):
    if stats['status'] != 'Up':
        return

    vmDigest = uuidDigest(stats['vmId'])

    cpuTotal = random.uniform(0, 100)
    cpuUser = random.uniform(0, cpuTotal)
    stats['cpuUser'] = '%.2f' % (cpuUser)
    stats['cpuSys'] = '%.2f' % (cpuTotal - cpuUser)
    stats['memUsage'] = str(random.randint(0, 100))

    # Disks
    for disk in stats['disks'].values():
        # Simulate some supersonic disks:
        disk['readRate'] = str(random.randint(0, 2**31))
        disk['writeRate'] = str(random.randint(0, 2**31))
        disk['readLatency'] = str(random.randint(0, 10**9))
        disk['writeLatency'] = str(random.randint(0, 10**9))
        disk['flushLatency'] = str(random.randint(0, 10**9))

    # Network:
    for net in stats['network'].values():
        net['rxDropped'] = str(random.randint(0, 2 * 31))
        net['rxErrors'] = str(random.randint(0, 2 * 31))
        net['txDropped'] = str(random.randint(0, 2 * 31))
        net['txErrors'] = str(random.randint(0, 2 * 31))

    # Fake guest-agent reports:
    stats['session'] = 'Unknown'
    if 'memoryStats' not in stats:
        stats['memoryStats'] = {}
        stats['memoryStats']['mem_total'] = str(
            (2**random.randint(0, 5)) * GiB)
    memUsed = int(random.uniform(0, int(stats['memoryStats']['mem_total'])))
    memUnused = int(random.uniform(0, memUsed))

    memoryStats = stats['memoryStats']
    memoryStats['mem_unused'] = str(memUnused)
    memoryStats['mem_free'] = str(memUnused)
    memoryStats['swap_in'] = str(random.randint(0, 1024))
    memoryStats['swap_out'] = str(random.randint(0, 1024))
    memoryStats['pageflt'] = str(random.randint(0, 1024))
    memoryStats['majflt'] = str(random.randint(0, 1024))

    # Generate mounts info
    diskUsage = []
    diskUsage.append(createDiskUsage('/', 'ext4', 20 * GiB))
    diskUsage.append(createDiskUsage('/boot', 'ext4', 1 * GiB))
    diskUsage.append(createDiskUsage('/home', 'ext4', 50 * GiB))
    # Add guest-specific number of extra mounts
    for i in range(next(vmDigest) % MAX_DYNAMIC_MOUNTS):
        diskUsage.append(createDiskUsage('/mount/dynamic-%d' % i, 'ext4',
                                         next(vmDigest) * GiB))
    stats['diskUsage'] = diskUsage

    # Each vm between 1 to 3 ifaces, not dynamic:
    netIfaces = []
    for i in range(1 + (next(vmDigest) % 3)):
        netif = {}
        hw = [next(vmDigest) for _ in range(6)]
        netif['hw'] = ETH_HW_ADDR_FORMAT % (hw[0], hw[1], hw[2], hw[3], hw[4],
                                            hw[5])
        inet = [next(vmDigest) for _ in range(4)]
        netif['inet'] = ['%d.%d.%d.%d' % (inet[0], inet[1], inet[2], inet[3])]
        # For simplicty purposes, the ipv6 addresses are transition from ipv4
        netif['inet6'] = ['0:0:0:0:0:ffff:%x%02x:%x%02x' % (inet[0], inet[1],
                                                            inet[2], inet[3])]
        netif['name'] = 'eth%d' % i

        netIfaces.append(netif)
    stats['netIfaces'] = netIfaces

    stats['guestFQDN'] = '%s.fakefqdn.com' % stats['vmId'].replace('-', '')

    stats['appsList'] = APP_LIST


if __name__ == '__main__':
    if config.getboolean('vars', 'fake_vmstats_enable'):
        statsList = hooking.read_json()
        for stats in statsList:
            randomizeRuntimeStats(stats)
        hooking.write_json(statsList)
