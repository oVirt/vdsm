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

import uuid
import xml.etree.ElementTree as ET

from vdsm.virt.containers import docker
from vdsm.virt.containers import runner
from vdsm.virt.containers import xmlfile

from monkeypatch import MonkeyPatchScope

from . import conttestlib


class RuntimeConfigurationTests(conttestlib.RunnableTestCase):

    def setUp(self):
        super(RuntimeConfigurationTests, self).setUp()
        self.vm_uuid = str(uuid.uuid4())
        self.base = docker.Runtime(self.vm_uuid)
        self.conf = {
            'mem': 4 * 1024 * 1024,  # KiB
            'path': '/random/path/to/disk/image',
            'net': 'ovirtmgmt',
            'runtime': 'docker',
            'image': 'redis',
        }

    def test_missing_content(self):
        root = ET.fromstring("<domain type='kvm' id='2'></domain>")
        self.assertRaises(xmlfile.ConfigError,
                          self.base.configure,
                          root)

    def test_missing_memory(self):
        root = ET.fromstring(conttestlib.only_disk_dom_xml())
        self.assertRaises(xmlfile.ConfigError,
                          self.base.configure,
                          root)

    def test_missing_disk(self):
        root = ET.fromstring(conttestlib.only_mem_dom_xml())
        self.assertRaises(xmlfile.ConfigError,
                          self.base.configure,
                          root)

    def test_disk_source_not_file(self):
        root = ET.fromstring(conttestlib.disk_file_malformed_dom_xml())
        self.assertRaises(xmlfile.ConfigError,
                          self.base.configure,
                          root)

    def test_volume_mapping(self):
        root = ET.fromstring(conttestlib.metadata_drive_map_dom_xml())
        self.assertNotRaises(self.base.configure, root)
        conf = self.base._run_conf
        self.assertEqual(conf.volume_mapping, {
            "data": "vda",
        })  # FIXME

    def test_bridge_down(self):
        root = ET.fromstring(conttestlib.bridge_down_dom_xml())
        base = docker.Runtime(self.vm_uuid)
        self.assertRaises(xmlfile.ConfigError,
                          base.configure,
                          root)

    def test_bridge_no_source(self):
        root = ET.fromstring(conttestlib.bridge_no_source_dom_xml())
        base = docker.Runtime(self.vm_uuid)
        self.assertRaises(xmlfile.ConfigError,
                          base.configure,
                          root)

    def test_config_present(self):
        root = ET.fromstring("""
        <domain type='kvm' id='2'>
          <maxMemory slots='16' unit='KiB'>{mem}</maxMemory>
          <metadata>
            <ovirtcnt:container
              xmlns:ovirtcnt="http://ovirt.org/vm/containers/1.0">
              <ovirtcnt:runtime>{runtime}</ovirtcnt:runtime>
              <ovirtcnt:image>{image}</ovirtcnt:image>
            </ovirtcnt:container>
          </metadata>
          <devices>
            <disk type='file' device='cdrom' snapshot='no'>
              <source file='{path}'>
              </source>
              <target dev='vdb' bus='virtio'/>
            </disk>
            <interface type="bridge">
              <mac address="00:1a:4a:16:01:57"/>
              <model type="virtio"/>
              <source bridge="{net}"/>
              <link state="up"/>
            </interface>
          </devices>
        </domain>""".format(**self.conf))
        self.assertNotRaises(self.base.configure, root)
        conf = self.base._run_conf
        # libvirt uses KiB
        self.assertEqual(conf.memory_size_mib, self.conf['mem'] / 1024.)
        self.assertEqual(conf.network, self.conf['net'])
        self.assertEqual(conf.image_path, self.conf['image'])

    # TODO: test error paths in configure()


class NonePath(object):
    def __init__(self):
        self.cmd = None


class DockerTests(conttestlib.RunnableTestCase):

    def test_created_not_running(self):
        rt = docker.Runtime()
        self.assertFalse(rt.running)

    def test_start_stop(self):
        with conttestlib.minimal_instance(
            docker.Runtime,
        ) as rt:
            self.assertTrue(rt.running)
        self.assertFalse(rt.running)

    def test_start_twice(self):
        with conttestlib.minimal_instance(
            docker.Runtime,
        ) as rt:
            self.assertRaises(runner.OperationFailed,
                              rt.start)

    def test_stop_not_started(self):
        rt = docker.Runtime()
        self.assertFalse(rt.running)
        self.assertRaises(runner.OperationFailed, rt.stop)

    def test_recover_succeed(self):
        rt = docker.Runtime()
        self.assertFalse(rt.running)
        rt.recover()
        self.assertTrue(rt.running)

    def test_recover_twice(self):
        rt = docker.Runtime()
        rt.recover()
        self.assertRaises(runner.OperationFailed, rt.recover)

    def test_available(self):
        self.assertTrue(docker.available())

    def test_not_available(self):
        with MonkeyPatchScope([(docker, '_DOCKER', NonePath())]):
            self.assertFalse(docker.available())


class NetworkTests(conttestlib.RunnableTestCase):

    def test_subnet(self):
        NAME = 'test'
        net = docker.Network(NAME)
        self.assertEqual(net.subnet, "10.1.0.0/20")

    def test_existing_false_before_load(self):
        NAME = 'test'
        net = docker.Network(NAME)
        self.assertFalse(net.existing)

    def test_load(self):
        NAME = 'ovirtmgmt'
        net = docker.Network(NAME)
        net.load()
        self.assertTrue(net.existing)

    def test_update(self):
        NAME = 'ovirtmgmt'
        net = docker.Network(NAME)
        net.update(
            nic='foonet0',
            gw='192.168.192.240',
            subnet='192.168.192.0',
            mask='26'
        )
        self.assertEqual(net.subnet, "192.168.192.0/26")

    def test_load_missing(self):
        NAME = 'notexists'
        net = docker.Network(NAME)
        net.load()
        self.assertFalse(net.existing)

    def test_save(self):
        # TODO
        pass

    def test_clear(self):
        # TODO
        pass

    def test_context_manager(self):
        # TODO
        pass
