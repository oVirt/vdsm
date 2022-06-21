#
# Copyright 2017-2020 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

from contextlib import contextmanager
import json
import libvirt
import libvirt_qemu
import logging

import pytest

from vdsm import utils
from vdsm.virt import qemuguestagent

from testlib import make_config
from testlib import VdsmTestCase as TestCaseBase
from monkeypatch import MonkeyClass, MonkeyPatchScope

from . import vmfakelib as fake


def _fake_qemuAgentCommand(domain, command, timeout, flags):
    if command == '{"execute": "guest-info"}':
        return json.dumps(
            {"return": {
                "version": "1.2.3",
                "supported_commands": [
                    {
                        "enabled": True,
                        "name": "guest-info",
                        "success-response": True
                    }, {
                        "enabled": False,
                        "name": "guest-exec",
                        "success-response": True
                    }]
            }})
    if command == '{"execute": "guest-get-devices"}':
        return json.dumps(
            {"return": [{
                # original API
                'driver-date': '2019-08-12',
                'driver-name': 'Red Hat VirtIO Ethernet Adapter',
                'driver-version': '100.80.104.17300',
                'address': {
                    'type': 'pci',
                    'data': {
                        'device-id': 4096,
                        'vendor-id': 6900,
                    }
                }
            }, {
                # API in 5.2
                'driver-date': 1565568000000000000,
                'driver-name': 'VirtIO Balloon Driver',
                'driver-version': '100.80.104.17300',
                'id': {
                    'type': 'pci',
                    'device-id': 4098,
                    'vendor-id': 6900
                }
            }, {
                # Duplicate of first entry
                'driver-date': '2019-08-12',
                'driver-name': 'Red Hat VirtIO Ethernet Adapter',
                'driver-version': '100.80.104.17300',
                'address': {
                    'type': 'pci',
                    'data': {
                        'device-id': 4096,
                        'vendor-id': 6900,
                    }
                }
            },
            ]})
    # Unknow command
    logging.error("Fake QEMU-GA cannot handle: %r", command)
    return '{"error": {"class": "CommandNotFound", "desc": "..."}}'


class FakeDomain(object):
    def interfaceAddresses(self, source):
        if source != libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT:
            return None
        ifdata = {
            'ens2': {'addrs': [
                {'addr': '192.168.124.216', 'prefix': 24, 'type': 0},
                {'addr': 'fe80::5054:ff:feed:9976', 'prefix': 64, 'type': 1}],
                'hwaddr': '52:54:00:ed:99:76'},
            'lo': {'addrs': [
                {'addr': '127.0.0.1', 'prefix': 8, 'type': 0},
                {'addr': '::1', 'prefix': 128, 'type': 1}],
                'hwaddr': '00:00:00:00:00:00'}}
        return ifdata

    def guestInfo(self, types, flags):
        return {
            'user.count': 2,
            'user.0.name': 'root',
            'user.1.name': 'frodo',
            'user.1.domain': 'hobbits',
            'os.id': 'rhel',
            'os.name': 'Red Hat Enterprise Linux Server',
            'os.pretty-name':
                'Red Hat Enterprise Linux Server 7.8 Beta (Maipo)',
            'os.version': '7.8 (Maipo)',
            'os.version-id': '7.8',
            'os.machine': 'x86_64',
            'os.variant': 'Server',
            'os.variant-id': 'server',
            'os.kernel-release': '3.10.0-1101.el7.x86_64',
            'os.kernel-version': '#1 SMP Sat Oct 5 04:50:26 EDT 2019',
            'timezone.name': 'EDT',
            'timezone.offset': -14400,
            'hostname': 'localhost.localdomain',
            'fs.count': 1,
            'fs.0.name': 'vda1',
            'fs.0.mountpoint': '/',
            'fs.0.fstype': 'xfs',
            'fs.0.disk.count': 1,
            'fs.0.disk.0.alias': 'vda',
            'fs.0.disk.0.serial': 'e7d27603-0a2e-47ab-8',
            'fs.0.disk.0.device': '/dev/vda1',
            'fs.0.total-bytes': 200,
            'fs.0.used-bytes': 100,
            'disk.count': 3,
            'disk.0.alias': 'vda',
            'disk.0.partition': False,
            'disk.0.serial': 'e7d27603-0a2e-47ab-8',
            'disk.0.name': '/dev/vda',
            'disk.1.alias': 'vdb',
            'disk.1.partition': False,
            'disk.1.name': '/dev/vdb',
            'disk.2.alias': 'vdc',
            'disk.2.partition': False,
            'disk.2.serial': '12345-67890',
            'disk.2.name': '/dev/vdc',
        }

    def guestVcpus(self, flags=0):
        return {
            'vcpus': '0-7',
            'online': '0-3',
            'offlinable': '0-7'
        }


class FakeGuestAgent(object):
    def __init__(self):
        self.guestStatus = None


class FakeVM(object):
    def __init__(self):
        self._dom = FakeDomain()
        self.guestAgent = FakeGuestAgent()

    @property
    def id(self):
        return "00000000-0000-0000-0000-000000000001"

    def qemu_agent_command(self, command, timeout, flags):
        return libvirt_qemu.qemuAgentCommand(
            self._dom, command, timeout, flags)

    @contextmanager
    def qga_context(self, timeout=-1):
        yield


def _dom_guestInfo(self, types, flags):
    return self._vm._dom.guestInfo(types, flags)


@MonkeyClass(libvirt_qemu, "qemuAgentCommand", _fake_qemuAgentCommand)
@MonkeyClass(qemuguestagent, 'config', make_config([
    ('guest_agent', 'periodic_workers', '1')
]))
@MonkeyClass(qemuguestagent.QemuGuestAgentDomain, 'guestInfo', _dom_guestInfo)
class QemuGuestAgentTests(TestCaseBase):
    def setUp(self):
        self.cif = fake.ClientIF()
        self.qga_poller = self.cif.qga_poller
        self.vm = FakeVM()
        self.qga_poller.update_caps(
            self.vm.id,
            {
                'version': '0.0-test',
                'commands': [
                    qemuguestagent._QEMU_ACTIVE_USERS_COMMAND,
                    qemuguestagent._QEMU_DEVICES_COMMAND,
                    qemuguestagent._QEMU_GUEST_INFO_COMMAND,
                    qemuguestagent._QEMU_FSINFO_COMMAND,
                    qemuguestagent._QEMU_HOST_NAME_COMMAND,
                    qemuguestagent._QEMU_NETWORK_INTERFACES_COMMAND,
                    qemuguestagent._QEMU_OSINFO_COMMAND,
                    qemuguestagent._QEMU_TIMEZONE_COMMAND,
                ]
            })
        self.qga_poller.channel_state_changed(
            self.vm.id, qemuguestagent.CHANNEL_CONNECTED, 0)

    def test_caps(self):
        """
        Make sure the capabilities are stored properly and the returned
        capabilities are stable.
        """
        c1 = {
            "version": "1.0",
            "commands": ["foo", "bar"],
        }
        c2 = utils.picklecopy(c1)
        c2["commands"].append("baz")
        self.qga_poller.update_caps(self.vm.id, c1)
        c3 = self.qga_poller.get_caps(self.vm.id)
        self.qga_poller.update_caps(self.vm.id, c2)
        assert c1 == c3
        assert c2 != c3

    def test_cmd_arrays(self):
        """
        Make sure the internal arrays are consistent.
        """
        assert frozenset(qemuguestagent._QEMU_COMMANDS.keys()) \
            .issubset(
                frozenset(qemuguestagent._QEMU_COMMAND_PERIODS.keys()))

    def test_failure(self):
        """ Make sure failure timestamp is set on errors. """
        def _qga_command_fail(*args, **kwargs):
            raise libvirt.libvirtError("Some error!")

        last = self.qga_poller.last_failure(self.vm.id)
        assert last == 0
        with MonkeyPatchScope([
                (libvirt_qemu, "qemuAgentCommand", _qga_command_fail)]):
            self.qga_poller.call_qga_command(
                self.vm,
                qemuguestagent._QEMU_GUEST_INFO_COMMAND)
        now = self.qga_poller.last_failure(self.vm.id)
        assert now > 0

    def test_no_failure(self):
        """ Make sure reset_failure() does not break on unknown VM ID """
        if self.vm.id in self.qga_poller._last_failure:
            del self.qga_poller._last_failure[self.vm.id]
        self.qga_poller.reset_failure(self.vm.id)

    def test_guest_info(self):
        """ Set and read guest info. """
        self.qga_poller.update_guest_info(
            self.vm.id, {"test-key": "test-value"})
        assert self.qga_poller.get_guest_info(self.vm.id)["test-key"] == \
            "test-value"
        # Test with invalid VM
        assert self.qga_poller.get_guest_info(
            "99999999-9999-9999-9999-999999999999") is None

    def test_capability_check(self):
        self.qga_poller.update_caps(
            self.vm.id,
            {"version": "0.0", "commands": []})
        self.qga_poller._qga_capability_check(self.vm)
        c = self.qga_poller.get_caps(self.vm.id)
        assert c['version'] == '1.2.3'
        assert 'guest-info' in c['commands']
        assert 'guest-exec' not in c['commands']

    def test_network_interfaces(self):
        info = self.qga_poller._qga_call_network_interfaces(self.vm)
        ifaces = info['netIfaces']
        iflo = [x for x in ifaces if x['name'] == 'lo'][0]
        ifens2 = [x for x in ifaces if x['name'] == 'ens2'][0]
        assert iflo == \
            {
                'hw': '00:00:00:00:00:00',
                'inet': ['127.0.0.1'],
                'inet6': ['::1'],
                'name': 'lo'
            }
        assert ifens2 == \
            {
                'hw': '52:54:00:ed:99:76',
                'inet': ['192.168.124.216'],
                'inet6': ['fe80::5054:ff:feed:9976'],
                'name': 'ens2'
            }

    def test_libvirt_guest_info(self):
        info = self.qga_poller._libvirt_get_guest_info(self.vm, 0xffffffff)
        # Disks/Filesystems
        assert info['disksUsage'][0] == {
            'path': '/',
            'total': '200',
            'used': '100',
            'fs': 'xfs',
        }
        assert info['diskMapping'] == {
            'e7d27603-0a2e-47ab-8': {'name': '/dev/vda'},
            '12345-67890': {'name': '/dev/vdc'},
        }
        # Hostname
        assert info['guestFQDN'] == 'localhost.localdomain'
        assert info['guestName'] == 'localhost.localdomain'
        # OS
        assert info['guestOs'] == '3.10.0-1101.el7.x86_64'
        assert info['guestOsInfo'] == {
            'type': 'linux',
            'arch': 'x86_64',
            'kernel': '3.10.0-1101.el7.x86_64',
            'distribution': 'Red Hat Enterprise Linux Server',
            'version': '7.8',
            'codename': 'Server',
        }
        # Timezone
        assert info['guestTimezone']['offset'] == -240
        assert info['guestTimezone']['zone'] == 'EDT'
        # Users
        assert info['username'] == 'root, frodo@hobbits'
        # fake appsList should exists
        assert self.qga_poller.get_guest_info(self.vm.id)['appsList'] == (
            'kernel-3.10.0-1101.el7.x86_64',
            'qemu-guest-agent-0.0-test'
        )

    def test_pci_devices(self):
        devices = self.qga_poller._qga_call_get_devices(self.vm)['pci_devices']
        # Ethernet is returned twice by the agent but should appear only
        # once in the list
        assert len(devices) == 2
        eth = [d for d in devices if d['device_id'] == 4096][0]
        assert eth == {
            'device_id': 4096,
            'driver_date': '2019-08-12',
            'driver_name': 'Red Hat VirtIO Ethernet Adapter',
            'driver_version': '100.80.104.17300',
            'vendor_id': 6900,
        }
        balloon = [d for d in devices if d['device_id'] == 4098][0]
        assert balloon == {
            'device_id': 4098,
            'driver_date': '2019-08-12',
            'driver_name': 'VirtIO Balloon Driver',
            'driver_version': '100.80.104.17300',
            'vendor_id': 6900,
        }

    def test_state_changes(self):
        self.qga_poller._channel_state[self.vm.id] = \
            qemuguestagent.CHANNEL_UNKNOWN
        self.qga_poller.channel_state_changed(
            self.vm.id, qemuguestagent.CHANNEL_CONNECTED, 0)
        assert self.qga_poller._channel_state[self.vm.id] == \
            qemuguestagent.CHANNEL_CONNECTED
        self.qga_poller.channel_state_changed(
            self.vm.id, qemuguestagent.CHANNEL_DISCONNECTED, 0)
        assert self.qga_poller._channel_state[self.vm.id] == \
            qemuguestagent.CHANNEL_DISCONNECTED
        self.qga_poller.channel_state_changed(
            self.vm.id, qemuguestagent.CHANNEL_CONNECTED, 0)
        assert self.qga_poller._channel_state[self.vm.id] == \
            qemuguestagent.CHANNEL_CONNECTED
        self.qga_poller.channel_state_changed(
            self.vm.id, qemuguestagent.CHANNEL_CONNECTED, 0)
        assert self.qga_poller._channel_state[self.vm.id] == \
            qemuguestagent.CHANNEL_CONNECTED
        # Test invalid values
        with pytest.raises(ValueError):
            self.qga_poller.channel_state_changed(
                self.vm.id, -100, 0)
        with pytest.raises(TypeError):
            self.qga_poller.channel_state_changed(
                self.vm.id, 'abc', 0)

    def test_guest_vcpus(self):
        info = self.qga_poller._qga_call_get_vcpus(self.vm)
        assert 'guestCPUCount' in info
        assert info['guestCPUCount'] == 4
