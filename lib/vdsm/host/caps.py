#
# Copyright 2011-2020 Red Hat, Inc.
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
from __future__ import division

import os
import logging

from vdsm import cpuinfo
from vdsm import host
from vdsm import hugepages
from vdsm import machinetype
from vdsm import numa
from vdsm import osinfo
from vdsm import taskset
from vdsm import utils
from vdsm.common import cache
from vdsm.common import commands
from vdsm.common import cpuarch
from vdsm.common import dsaversion
from vdsm.common import hooks
from vdsm.common import hostdev
from vdsm.common import libvirtconnection
from vdsm.common import supervdsm
from vdsm.common import xmlutils
from vdsm.config import config
from vdsm.host import rngsources
from vdsm.storage import backends
from vdsm.storage import constants as sc
from vdsm.storage import exception as se
from vdsm.storage import hba
from vdsm.storage import managedvolume

try:
    import ovirt_hosted_engine_ha.client.client as haClient
except ImportError:
    haClient = None


def _parseKeyVal(lines, delim='='):
    d = {}
    for line in lines:
        kv = line.split(delim, 1)
        if len(kv) != 2:
            continue
        k, v = map(lambda x: x.strip(), kv)
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
    numa.update()
    caps = {}
    cpu_topology = numa.cpu_topology()

    caps['kvmEnabled'] = str(os.path.exists('/dev/kvm')).lower()

    if config.getboolean('vars', 'report_host_threads_as_cores'):
        caps['cpuCores'] = str(cpu_topology.threads)
    else:
        caps['cpuCores'] = str(cpu_topology.cores)

    caps['cpuThreads'] = str(cpu_topology.threads)
    caps['cpuSockets'] = str(cpu_topology.sockets)
    caps['onlineCpus'] = ','.join(
        [str(cpu_id) for cpu_id in cpu_topology.online_cpus]
    )

    caps['cpuTopology'] = [
        {
            'cpu_id': cpu.cpu_id,
            'numa_cell_id': cpu.numa_cell_id,
            'socket_id': cpu.socket_id,
            'die_id': cpu.die_id,
            'core_id': cpu.core_id,
        } for cpu in numa.cpu_info()]

    caps['cpuSpeed'] = cpuinfo.frequency()
    caps['cpuModel'] = cpuinfo.model()
    caps['cpuFlags'] = ','.join(_getFlagsAndFeatures())
    caps['vdsmToCpusAffinity'] = list(taskset.get(os.getpid()))

    caps.update(dsaversion.version_info())

    proxy = supervdsm.getProxy()
    net_caps = proxy.network_caps()
    caps.update(net_caps)
    caps['ovnConfigured'] = proxy.is_ovn_configured()

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

    caps['memSize'] = str(utils.readMemInfo()['MemTotal'] // 1024)
    caps['reservedMem'] = str(config.getint('vars', 'host_mem_reserve') +
                              config.getint('vars', 'extra_mem_reserve'))
    caps['guestOverhead'] = config.get('vars', 'guest_ram_overhead')

    caps['rngSources'] = rngsources.list_available()

    caps['numaNodes'] = dict(numa.topology())
    caps['numaNodeDistance'] = dict(numa.distances())
    caps['autoNumaBalancing'] = numa.autonuma_status()

    caps['selinux'] = osinfo.selinux_status()

    caps['liveSnapshot'] = 'true'
    caps['liveMerge'] = 'true'
    caps['kdumpStatus'] = osinfo.kdump_status()
    caps["deferred_preallocation"] = True

    caps['hostdevPassthrough'] = str(hostdev.is_supported()).lower()
    # TODO This needs to be removed after adding engine side support
    # and adding gdeploy support to enable libgfapi on RHHI by default
    caps['additionalFeatures'] = ['libgfapi_supported']
    if osinfo.glusterEnabled:
        from vdsm.gluster.api import glusterAdditionalFeatures
        caps['additionalFeatures'].extend(glusterAdditionalFeatures())
    caps['hostedEngineDeployed'] = _isHostedEngineDeployed()
    caps['hugepages'] = hugepages.supported()
    caps['kernelFeatures'] = osinfo.kernel_features()
    caps['vncEncrypted'] = _isVncEncrypted()
    caps['backupEnabled'] = True
    caps['coldBackupEnabled'] = True
    caps['clearBitmapsEnabled'] = True
    caps['fipsEnabled'] = _getFipsEnabled()
    try:
        caps['boot_uuid'] = osinfo.boot_uuid()
    except Exception:
        logging.exception("Can not find boot uuid")
    caps['tscFrequency'] = _getTscFrequency()
    caps['tscScaling'] = _getTscScaling()

    try:
        caps["connector_info"] = managedvolume.connector_info()
    except se.ManagedVolumeNotSupported as e:
        logging.info("managedvolume not supported: %s", e)
    except se.ManagedVolumeHelperFailed as e:
        logging.exception("Error getting managedvolume connector info: %s", e)

    # Which domain versions are supported by this host.
    caps["domain_versions"] = sc.DOMAIN_VERSIONS

    caps["supported_block_size"] = backends.supported_block_size()
    caps["cd_change_pdiv"] = True
    caps["refresh_disk_supported"] = True

    return caps


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


def _isVncEncrypted():
    """
    If VNC is configured to use encrypted connections, libvirt's qemu.conf
    contains the following flag:
        vnc_tls = 1
    """
    try:
        return supervdsm.getProxy().check_qemu_conf_contains('vnc_tls', '1')
    except:
        logging.error("Supervdsm was not able to read VNC TLS config. "
                      "Check supervdsmd log for details.")
    return False


@cache.memoized
def _getTscFrequency():
    """
    Read TSC Frequency from libvirt. This is only available in
    libvirt >= 5.5.0 (and 4.5.0-21 for RHEL7)
    """
    conn = libvirtconnection.get()
    caps = xmlutils.fromstring(conn.getCapabilities())
    counter = caps.findall("./host/cpu/counter[@name='tsc']")
    if len(counter) > 0:
        return counter[0].get('frequency')
    logging.debug('No TSC counter returned by Libvirt')
    return ""


def _getFipsEnabled():
    """
    Read FIPS status using sysctl
    """
    SYSCTL_FIPS_COMMAND = ["/usr/sbin/sysctl", "crypto.fips_enabled"],

    try:
        output = commands.run(*SYSCTL_FIPS_COMMAND)
        enabled = output.split(b'=')[1].strip()
        return enabled == b'1'
    except Exception as e:
        logging.error("Could not read FIPS status with sysctl: %s", e)
        return False


def _getTscScaling():
    """
    Read TSC Scaling from libvirt. This is only available in
    libvirt >= 5.5.0 (and 4.5.0-21 for RHEL7)
    """
    conn = libvirtconnection.get()
    caps = xmlutils.fromstring(conn.getCapabilities())
    counter = caps.findall("./host/cpu/counter[@name='tsc']")
    if len(counter) > 0:
        return counter[0].get('scaling') == 'yes'
    logging.debug('No TSC counter returned by Libvirt')
    return False


def _getFlagsAndFeatures():
    """
    Read CPU flags from cpuinfo and CPU features from domcapabilities,
    and combine them into a single list.
    """
    # We need to use both flags (cpuinfo.flags()) and domcapabilites
    # (machinetype.cpu_features) because they return different sets
    # of flags (domcapabilities also return the content of
    # arch_capabilities). The sets overlap, so we convert
    # list -> set -> list to remove duplicates.
    flags_and_features = list(set(cpuinfo.flags() +
                                  machinetype.cpu_features()))
    cpu_models = machinetype.compatible_cpu_models()
    # Easier to add here than in Engine:
    # If -IBRS suffix is present in any of the compatible CPU models,
    # we can assume spec_ctrl feature.
    for model in cpu_models:
        if model.endswith('-IBRS'):
            spec_ctrl = 'spec_ctrl'
            if spec_ctrl not in flags_and_features:
                flags_and_features.append(spec_ctrl)
            break
    return flags_and_features + cpu_models
