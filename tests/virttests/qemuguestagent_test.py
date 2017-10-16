#
# Copyright 2017 Red Hat, Inc.
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

import json
import libvirt
import libvirt_qemu
import logging

from vdsm import schedule, utils
from vdsm.common.time import monotonic_time
from vdsm.virt import qemuguestagent

from testlib import make_config
from testlib import VdsmTestCase as TestCaseBase
from monkeypatch import MonkeyClass, MonkeyPatchScope
import vmfakelib as fake


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
    if command == '{"execute": "guest-get-host-name"}':
        return json.dumps(
            {"return": {
                "host-name": "test-host",
            }})
    if command == '{"execute": "guest-get-osinfo"}':
        return json.dumps(
            {"return": {
                "id": "fedora",
                "kernel-release": "4.13.9-300.fc27.x86_64",
                "kernel-version": "#1 SMP Mon Oct 23 13:41:58 UTC 2017",
                "machine": "x86_64",
                "name": "Fedora",
                "pretty-name": "Fedora 27 (Cloud Edition)",
                "variant": "Cloud Edition",
                "variant-id": "cloud",
                "version": "27 (Cloud Edition)",
                "version-id": "27",
            }})
    if command == '{"execute": "guest-get-timezone"}':
        return json.dumps(
            {"return": {
                "zone": "CET",
                "offset": 3600
            }})
    if command == '{"execute": "guest-get-users"}':
        return json.dumps(
            {"return": [{
                "login-time": 1515975891.567572,
                "domain": "DESKTOP-NG2EVRF",
                "user": "Calvin"
            }, {
                "login-time": 1515975891.567572,
                "user": "Hobbes"
            }]})
    # Unknow command
    logging.error("Fake QEMU-GA cannot handle: %r", command)
    return '{"error": {"class": "CommandNotFound", "desc": "..."}}'


class FakeVM(object):
    @property
    def id(self):
        return "00000000-0000-0000-0000-000000000001"

    @property
    def _dom(self):
        return None


@MonkeyClass(libvirt_qemu, "qemuAgentCommand", _fake_qemuAgentCommand)
@MonkeyClass(qemuguestagent, 'config', make_config([
    ('guest_agent', 'periodic_workers', '1')
]))
class QemuGuestAgentTests(TestCaseBase):
    def setUp(self):
        self.cif = fake.ClientIF()
        self.scheduler = schedule.Scheduler(name="test.Scheduler",
                                            clock=monotonic_time)
        self.scheduler.start()
        self.log = logging.getLogger("test")
        self.qga_poller = qemuguestagent.QemuGuestAgentPoller(
            self.cif, self.log, self.scheduler)
        self.vm = FakeVM()
        self.qga_poller.update_caps(
            self.vm.id,
            {
                'version': '0.0-test',
                'commands': [
                    qemuguestagent._QEMU_ACTIVE_USERS_COMMAND,
                    qemuguestagent._QEMU_GUEST_INFO_COMMAND,
                    qemuguestagent._QEMU_HOST_NAME_COMMAND,
                    qemuguestagent._QEMU_OSINFO_COMMAND,
                    qemuguestagent._QEMU_TIMEZONE_COMMAND,
                ]
            })

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
        self.assertEqual(c1, c3)
        self.assertNotEqual(c2, c3)

    def test_failure(self):
        """ Make sure failure timestamp is set on errors. """
        def _qga_command_fail(*args, **kwargs):
            raise libvirt.libvirtError("Some error!")

        last = self.qga_poller.last_failure(self.vm.id)
        self.assertIsNone(last)
        with MonkeyPatchScope([
                (libvirt_qemu, "qemuAgentCommand", _qga_command_fail)]):
            self.qga_poller.call_qga_command(
                self.vm,
                qemuguestagent._QEMU_GUEST_INFO_COMMAND)
        now = self.qga_poller.last_failure(self.vm.id)
        self.assertIsNotNone(now)
        self.assertNotEqual(last, now)

    def test_guest_info(self):
        """ Set and read guest info. """
        self.qga_poller.update_guest_info(
            self.vm.id, {"test-key": "test-value"})
        self.assertEqual(
            self.qga_poller.get_guest_info(self.vm.id)["test-key"],
            "test-value")
        # Test with invalid VM
        self.assertIsNone(self.qga_poller.get_guest_info(
            "99999999-9999-9999-9999-999999999999"))

    def test_capability_check(self):
        self.qga_poller.update_caps(
            self.vm.id,
            {"version": "0.0", "commands": []})
        caps = qemuguestagent.CapabilityCheck(self.vm, self.qga_poller)
        caps._execute()
        c = self.qga_poller.get_caps(self.vm.id)
        self.assertEqual(c['version'], '1.2.3')
        self.assertTrue('guest-info' in c['commands'])
        self.assertFalse('guest-exec' in c['commands'])

    def test_active_users(self):
        c = qemuguestagent.ActiveUsersCheck(self.vm, self.qga_poller)
        c._execute()
        self.assertEqual(
            self.qga_poller.get_guest_info(self.vm.id),
            {'username': 'Calvin@DESKTOP-NG2EVRF, Hobbes'})

    def test_system_info(self):
        c = qemuguestagent.SystemInfoCheck(self.vm, self.qga_poller)
        c._execute()
        self.assertEqual(
            self.qga_poller.get_guest_info(self.vm.id),
            {
                'guestName': 'test-host',
                'guestFQDN': 'test-host',
                'guestOs': '4.13.9-300.fc27.x86_64',
                'guestOsInfo': {
                    'kernel': '4.13.9-300.fc27.x86_64',
                    'arch': 'x86_64',
                    'version': '27',
                    'distribution': 'Fedora',
                    'type': 'linux',
                    'codename': 'Cloud Edition'
                },
                'guestTimezone': {
                    'offset': 60,
                    'zone': 'CET',
                },
            })
