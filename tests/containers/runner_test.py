#
# Copyright 2015-2016 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 2 of the License, or
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

from functools import wraps
import uuid

from vdsm.virt.containers import command
from vdsm.virt.containers import runner

from monkeypatch import MonkeyPatchScope

from . import conttestlib


def patch_function(module, name, that):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kw):
            with MonkeyPatchScope([(module, name, that)]):
                return f(*args, **kw)
        return wrapper
    return decorator


class RuntimeListTests(conttestlib.RunnableTestCase):
    def test_pristine(self):
        runr = runner.Runner('testing')
        conts = list(runr.get_all())
        self.assertEqual(conts, [])

    # we need something we are confident can't exist
    @patch_function(runner, 'PREFIX', str(uuid.uuid4()))
    def test_no_output(self):
        runr = runner.Runner('testing')
        conts = list(runr.get_all())
        self.assertEqual(conts, [])

    # TODO: add test with fake correct output
    def test_single_service(self):
        VM_UUID = 'd7a0005e-ee05-4e61-9fbe-d2e93d59327c'

        def fake_output(prefix):
            tmpl = conttestlib.read_test_data('systemctl_vdsm_service.txt')
            return tmpl % VM_UUID

        with MonkeyPatchScope([(command, 'systemctl_list', fake_output)]):
            runr = runner.Runner('testing')
            conts = list(runr.get_all())
            self.assertEqual(conts, [VM_UUID])

    def test__parse_systemctl_one_service(self):
        output = conttestlib.read_test_data('systemctl_foobar_service.txt')
        names = list(runner._parse_systemctl_list_units(output))
        self.assertEqual(names, ["foobar"])

    def test__parse_systemctl_empty_output(self):
        output = ""
        names = list(runner._parse_systemctl_list_units(output))
        self.assertEqual(names, [])

    def test__parse_systemctl_corrupted_output(self):
        output = """
foobar.service    somehow messed
"""
        names = list(runner._parse_systemctl_list_units(output))
        self.assertEqual(names, [])

    def test__parse_systemctl_no_services(self):
        output = conttestlib.read_test_data('systemctl_list.txt')
        names = list(runner._parse_systemctl_list_units(output))
        self.assertEqual(names, [])


class RunnerTests(conttestlib.RunnableTestCase):

    def test_created_not_running(self):
        runr = runner.Runner('testing')
        self.assertFalse(runr.running)

    def test_run_default_conf(self):
        unit_name = 'testing'
        runr = runner.Runner(unit_name)
        cmd = ('/bin/sleep', '42m',)
        runr.start(*cmd)
        self.assertTrue(runr.running)

        meth, args, kwargs = self.svdsm.__calls__[-1]
        self.assertEqual(meth, 'systemd_run')
        self.assertEqual(kwargs, {})
        self.assertEqual(args[-len(cmd):], cmd)

    def test_stop(self):
        unit_name = 'testing'
        runr = runner.Runner(unit_name)
        runr.stop()

        expected = (unit_name,)
        meth, args, kwargs = self.svdsm.__calls__[-1]
        self.assertEqual(meth, 'systemctl_stop')
        self.assertEqual(kwargs, {})
        self.assertEqual(args[-len(expected):], expected)
