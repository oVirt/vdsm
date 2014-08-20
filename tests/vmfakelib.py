#
# Copyright IBM Corp. 2012
# Copyright 2013-2014 Red Hat, Inc.
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

from contextlib import contextmanager

import libvirt

from vdsm import constants
from vdsm import libvirtconnection

import caps
from virt import vm

from testlib import namedTemporaryDir
from monkeypatch import MonkeyPatchScope


class Connection:
    def __init__(self, *args):
        pass

    def domainEventRegisterAny(self, *arg):
        pass

    def listAllNetworks(self, *args):
        return []


class ClientIF(object):
    def __init__(self, *args, **kwargs):
        self.channelListener = None
        self.vmContainer = {}


class Domain(object):
    def __init__(self, xml='',
                 virtError=libvirt.VIR_ERR_OK,
                 domState=libvirt.VIR_DOMAIN_RUNNING,
                 vmId=''):
        self._xml = xml
        self.devXml = ''
        self._virtError = virtError
        self._metadata = ""
        self._io_tune = {}
        self._domState = domState
        self._vmId = vmId
        self.calls = {}

    def _failIfRequested(self):
        if self._virtError != libvirt.VIR_ERR_OK:
            err = libvirt.libvirtError(defmsg='')
            err.err = [self._virtError]
            raise err

    def UUIDString(self):
        return self._vmId

    def info(self):
        self._failIfRequested()
        return (self._domState, )

    def XMLDesc(self, unused):
        return self._xml

    def updateDeviceFlags(self, devXml, unused):
        self.devXml = devXml

    def vcpusFlags(self, flags):
        return -1

    def metadata(self, type, uri, flags):
        self._failIfRequested()

        if not self._metadata:
            e = libvirt.libvirtError("No metadata")
            e.err = [libvirt.VIR_ERR_NO_DOMAIN_METADATA]
            raise e
        return self._metadata

    def setMetadata(self, type, xml, prefix, uri, flags):
        self._metadata = xml

    def schedulerParameters(self):
        return {'vcpu_quota': vm._NO_CPU_QUOTA,
                'vcpu_period': vm._NO_CPU_PERIOD}

    def setBlockIoTune(self, name, io_tune, flags):
        self._io_tune[name] = io_tune
        return 1

    def setMemory(self, target):
        self._failIfRequested()
        self.calls['setMemory'] = (target,)


class GuestAgent(object):
    def __init__(self):
        self.guestDiskMapping = {}

    def getGuestInfo(self):
        return {
            'username': 'Unknown',
            'session': 'Unknown',
            'memUsage': 0,
            'appsList': [],
            'guestIPs': '',
            'guestFQDN': '',
            'disksUsage': [],
            'netIfaces': [],
            'memoryStats': {},
            'guestCPUCount': -1}


@contextmanager
def VM(params=None, devices=None, runCpu=False,
       arch=caps.Architecture.X86_64, status=None):
    with namedTemporaryDir() as tmpDir:
        with MonkeyPatchScope([(constants, 'P_VDSM_RUN', tmpDir + '/'),
                               (libvirtconnection, 'get', Connection)]):
            vmParams = {'vmId': 'TESTING'}
            vmParams.update({} if params is None else params)
            cif = ClientIF()
            fake = vm.Vm(cif, vmParams)
            cif.vmContainer[fake.id] = fake
            fake.arch = arch
            fake.guestAgent = GuestAgent()
            fake.conf['devices'] = [] if devices is None else devices
            fake._guestCpuRunning = runCpu
            if status is not None:
                fake._lastStatus = status
            yield fake
