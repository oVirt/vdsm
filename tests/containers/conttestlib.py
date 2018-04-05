#
# Copyright 2015-2017 Red Hat, Inc.
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

from contextlib import contextmanager
import gzip
import logging
import os
import os.path
import shutil
import tarfile
import tempfile
import uuid
import xml.etree.ElementTree as ET

from vdsm.common import cmdutils
from vdsm.common import commands

import vdsm.virt.containers.cgroups
import vdsm.virt.containers.command
import vdsm.virt.containers.docker
import vdsm.virt.containers.domain
import vdsm.virt.containers.doms
import vdsm.virt.containers.runner
import vdsm.virt.containers.xmlfile

from monkeypatch import MonkeyPatchScope, Patch
from testlib import VdsmTestCase as TestCase
from testlib import recorded


class FakeRuntime(vdsm.virt.containers.docker.Runtime):

    _log = logging.getLogger('virt.containers.runtime.Fake')

    NAME = 'fake'

    _PREFIX = 'fake-'

    def __init__(self, rt_uuid=None):
        super(FakeRuntime, self).__init__(rt_uuid)
        self._log.debug('fake runtime %s', self._uuid)
        self._running = False
        self._actions = {
            'start': 0,
            'stop': 0,
            'resync': 0,
        }

    @property
    def actions(self):
        return self._actions.copy()

    @property
    def running(self):
        return self._running

    def start(self, target=None):
        if self.running:
            raise vdsm.virt.containers.runner.OperationFailed(
                'already running')

        self._actions['start'] += 1
        self._running = True

    def stop(self):
        if not self.running:
            raise vdsm.virt.containers.runner.OperationFailed(
                'not running')

        self._actions['stop'] += 1
        self._running = False

    def resync(self):
        self._actions['resync'] += 1


TEMPDIR = '/tmp'


@contextmanager
def named_temp_dir(base=TEMPDIR):
    tmp_dir = tempfile.mkdtemp(dir=base)
    try:
        yield tmp_dir
    finally:
        shutil.rmtree(tmp_dir)


class FakeCommands(object):

    def __init__(self):
        self._docker = cmdutils.CommandPath('docker',
                                            *self.paths('docker'))
        self._systemctl = cmdutils.CommandPath('systemctl',
                                               *self.paths('systemctl'))
        self._systemd_run = cmdutils.CommandPath('systemd-run',
                                                 *self.paths('systemd-run'))

    def paths(self, exe):
        return [os.path.join(path, exe) for path in(
            './containers/fake/bin', './tests/containers/fake/bin',
        )]

    @property
    def systemctl(self):
        return self._systemctl

    @property
    def docker(self):
        return self._docker


class FakeSuperVdsm(object):

    def __init__(self, exes):
        self._exes = exes

    # let's fake supervdsm as well
    def getProxy(self):
        return self

    @recorded
    def docker_net_inspect(self, network):
        return commands.execCmd([
            self._exes.docker.cmd,
            'network',
            'inspect',
            network,
        ], raw=True)

    @recorded
    def docker_net_create(self, subnet, gw, nic, network):
        data = '%s %s %s %s\n' % (subnet, gw, nic, network)
        return 0, data, ''

    @recorded
    def docker_net_remove(self, network):
        return 0, network, ''

    @recorded
    def systemctl_stop(self, name):
        return commands.execCmd([
            self._exes.systemctl.cmd,
            'stop',
            name,
        ], raw=True)

    @recorded
    def systemd_run(self, unit_name, cgroup_slice, *args):
        return 0, '', ''


class RunnableTestCase(TestCase):

    def setUp(self):
        clear_doms()
        self.guid = uuid.uuid4()
        self.run_dir = tempfile.mkdtemp()
        self.net_dir = os.path.join(self.run_dir, 'etc', 'net.d')
        os.makedirs(self.net_dir)
        self.cont_dir = os.path.join(self.run_dir, 'containers')
        os.makedirs(self.cont_dir)
        self.exes = FakeCommands()
        self.svdsm = FakeSuperVdsm(self.exes)
        self.patch = Patch([
            (vdsm.virt.containers.xmlfile,
                'STATE_DIR', os.path.join(self.run_dir, 'containers')),
            (vdsm.virt.containers.command,
                'supervdsm', self.svdsm),
            (vdsm.virt.containers.command,
                '_SYSTEMCTL', self.exes.systemctl),
            (vdsm.virt.containers.docker,
                '_DOCKER', self.exes.docker),
        ])
        self.patch.apply()
        vdsm.virt.containers.docker.setup_network(
            'test', 'brtest', 'eth1', '192.168.0.1', '192.168.0.0', '24'
        )

    def tearDown(self):
        self.patch.revert()
        shutil.rmtree(self.run_dir)


def clear_doms():
    with vdsm.virt.containers.doms._lock:
        vdsm.virt.containers.doms._doms.clear()


def clear_events(handler):
    with handler._lock:
        handler.events.clear()


@contextmanager
def move_into(path):
    oldpath = os.getcwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(oldpath)


class CgroupTestCase(TestCase):

    def setUp(self):
        self.pid = 0
        testdir = os.path.dirname(os.path.abspath(__file__))
        self.root = os.path.join(testdir, 'fake')

        self.procfsroot = os.path.join(
            self.root, vdsm.virt.containers.cgroups.PROCFS
        )
        self.cgroupfsroot = os.path.join(
            self.root, vdsm.virt.containers.cgroups.CGROUPFS
        )

        with move_into(self.root):
            cgroupsdata = os.path.join(self.root, 'cgroups.tgz')
            with gzip.GzipFile(cgroupsdata) as gz:
                tar = tarfile.TarFile(fileobj=gz)
                tar.extractall()

        self.patch = Patch([
            (vdsm.virt.containers.cgroups,
                '_PROCBASE', self.procfsroot),
            (vdsm.virt.containers.cgroups,
                '_CGROUPBASE', self.cgroupfsroot),
        ])
        self.patch.apply()

    def tearDown(self):
        self.patch.revert()
        shutil.rmtree(self.procfsroot)
        shutil.rmtree(self.cgroupfsroot)


@contextmanager
def tmp_run_dir():
    with named_temp_dir() as tmp_dir:
        with MonkeyPatchScope([
            (vdsm.virt.containers.xmlfile,
                'STATE_DIR', os.path.join(tmp_dir, 'containers')),
        ]):
            os.mkdir(os.path.join(tmp_dir, 'containers'))  # FIXME
            yield tmp_dir


@contextmanager
def fake_runtime_domain():
    with MonkeyPatchScope([
        (vdsm.virt.containers.docker, 'Runtime', FakeRuntime),
    ]):
        with tmp_run_dir():
            dom = vdsm.virt.containers.domain.Domain.create(
                minimal_dom_xml(),
            )
            yield dom


def minimal_dom_xml(vm_uuid=None):
    data = read_test_data('minimal_dom.xml')
    vm_uuid = str(uuid.uuid4()) if vm_uuid is None else vm_uuid
    return data.format(vm_uuid=vm_uuid)


def full_dom_xml(vm_uuid=None):
    data = read_test_data('full_dom.xml')
    vm_uuid = str(uuid.uuid4()) if vm_uuid is None else vm_uuid
    return data.format(vm_uuid=vm_uuid)


def only_disk_dom_xml():
    return read_test_data('only_disk.xml')


def only_mem_dom_xml():
    return read_test_data('only_mem.xml')


def disk_dev_dom_xml():
    return read_test_data('disk_dev.xml')


def disk_file_malformed_dom_xml():
    return read_test_data('disk_file_malformed.xml')


def bridge_down_dom_xml():
    return read_test_data('bridge_down.xml')


def bridge_no_source_dom_xml():
    return read_test_data('bridge_no_source.xml')


def metadata_drive_map_dom_xml():
    return read_test_data('metadata_drive_map.xml')


def read_test_data(name):
    testdir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(testdir, 'data', name)
    with open(path, 'rt') as src:
        return src.read()


@contextmanager
def minimal_instance(klass):
    with tmp_run_dir():
        inst = klass()
        root = ET.fromstring(minimal_dom_xml())
        inst.configure(root)
        inst.start()
        try:
            yield inst
        finally:
            inst.stop()
