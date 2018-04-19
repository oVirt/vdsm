#
# Copyright 2016-2017 Red Hat, Inc.
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

import collections
import json
import logging
import uuid

from vdsm.common import cmdutils
from vdsm.config import config

from . import command
from . import runner
from . import xmlfile


_DOCKER = cmdutils.CommandPath("docker",
                               "/bin/docker",
                               "/usr/bin/docker",
                               )


# TODO: networking
_RunConfig = collections.namedtuple(
    '_RunConfig', ['image_path', 'volume_paths', 'volume_mapping',
                   'memory_size_mib', 'network'])


class RunConfig(_RunConfig):

    @classmethod
    def from_domain(cls, dom):
        mem = dom.memory()
        path = dom.image()
        volumes = dom.volumes()
        mapping = dom.drives_map()
        try:
            net = dom.network()
        except xmlfile.ConfigError:
            net = config.get('containers', 'network_name')
        return cls(path, volumes, mapping, mem, net)


class Runtime(object):

    _log = logging.getLogger('virt.containers.runtime.docker')

    def __init__(self, rt_uuid=None):
        self._uuid = (
            uuid.uuid4() if rt_uuid is None else
            uuid.UUID(rt_uuid)
        )
        self._runner = runner.Runner(self.unit_name)
        self._run_conf = None
        self._log.debug('docker runtime %s', self._uuid)
        self._running = False

    def _run_cmdline(self, target=None):
        image = self._run_conf.image_path if target is None else target
        return [
            _DOCKER.cmd,
            'run',
            '--name=%s' % self.unit_name,
            '--net=%s' % self._run_conf.network,
            image,
        ]

    @property
    def running(self):
        return self._runner.running

    @property
    def uuid(self):
        return str(self._uuid)

    @property
    def unit_name(self):
        return "%s%s" % (runner.PREFIX, self.uuid)

    def start(self, target=None):
        if self.running:
            raise runner.OperationFailed('already running')
        self._runner.start(*self._run_cmdline(target))

    def stop(self):
        if not self.running:
            raise runner.OperationFailed('not running')
        self._runner.stop()

    def configure(self, xml_tree):
        self._log.debug('configuring runtime %r', self.uuid)
        dom = xmlfile.DomainParser(xml_tree, self._uuid, self._log)
        self._run_conf = RunConfig.from_domain(dom)
        self._log.debug('configured runtime %s: %s',
                        self.uuid, self._run_conf)

    def recover(self):
        # called in the recovery flow
        if self.running:
            raise runner.OperationFailed('already running')
        self._runner.recover()


class Network(object):

    _log = logging.getLogger('virt.containers.runtime.docker')

    def __init__(self, name=None):
        self._name = name or config.get(
            'containers', 'network_name')
        self._gw = config.get('containers', 'network_gateway')
        self._nic = config.get('containers', 'network_interface')
        self._subnet = config.get('containers', 'network_subnet')
        self._mask = config.getint('containers', 'network_mask')
        self._existing = False

    def __enter__(self):
        self.load()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.save()

    @property
    def existing(self):
        return self._existing

    @property
    def subnet(self):
        return '%s/%s' % (self._subnet, self._mask)

    def update(self, bridge=None, nic=None, gw=None, subnet=None, mask=None):
        if nic:
            self._nic = nic
        if gw:
            self._gw = gw
        if subnet:
            self._subnet = subnet
        if mask:
            self._mask = mask

    def load(self):
        try:
            out = command.docker_net_inspect(network=self._name)
            conf = json.loads(out.decode('utf-8'))
        except command.Failed:
            self._log.debug('config: cannot load %r, ignored', self._name)
        else:
            self._update_from_conf(conf)
        return self

    def save(self):
        return command.docker_net_create(
            subnet=self.subnet, gw=self._gw, nic=self._nic,
            network=self._name)

    def _update_from_conf(self, conf):
        if not conf:
            return
        try:
            self._nic = conf[0]['Options']['parent']
            ipam = conf[0]['IPAM']['Config'][0]  # shortcut
            self._gw = ipam['Gateway']
            self._subnet, mask = ipam['Subnet'].split('/')
            self._mask = int(mask)
        except KeyError as exc:
            self._log.warning(
                'missing configuration item (%s), skipped', str(exc))
        else:
            self._existing = True


def available():
    return (_DOCKER.cmd is not None)


def setup_network(name, bridge, nic, gw, subnet, mask):
    with Network(name) as net:
        net.update(nic=nic, gw=gw, subnet=subnet, mask=mask)
