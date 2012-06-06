#
# Copyright 2012 Zhou Zheng Sheng, IBM Corporation
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

import os
from tempfile import mkstemp
from contextlib import contextmanager
from testrunner import VdsmTestCase as TestCaseBase

import vdsClient


@contextmanager
def configFile(args):
    fd, path = mkstemp()
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(args))

    try:
        yield path
    finally:
        os.unlink(path)


class fakeXMLRPCServer(object):
    def create(self, params):
        return params


def fakeExecAndExit(response, parameterName=None):
    return response


class vdsClientTest(TestCaseBase):
    def testCreateArgumentParsing(self):
        serv = vdsClient.service()
        fakeServer = fakeXMLRPCServer()
        serv.s = fakeServer
        serv.ExecAndExit = fakeExecAndExit

        plainArgs = ['vmId=209b27e4-aed3-11e1-a547-00247edb4743', 'vmType=kvm',
                     'kvmEnable=true', 'memSize=1024',
                     'macAddr=52:54:00:7d:96:bd', 'nicModel=virtio',
                     'bridge=virbr0', 'display=vnc',
                     'cdrom=/path/to/some.iso', 'boot=c', 'vmName=rhel62vdsm',
                     'smp=2', 'acpiEnable=True']
        nestArgs = ['drive=pool:pooid,domain:domainpi,image:imageid,'
                    'volume:volumeid,boot:true,format:cow',
                    'devices={device:ide,type:controller}',
                    'devices={nicModel:virtio,macAddr:5F:45:00:95:F6:3F,'
                    'network:virbr0,alias:net0,address:{slot:0x03,bus:0x00,'
                    'domain:0x0000,type:pci,function:0x0}}',
                    'cpuPinning={0:0,1:1}']
        allArgs = plainArgs + nestArgs

        expectResult = {'acpiEnable': 'True',
                        'boot': 'c',
                        'bridge': 'virbr0',
                        'cdrom': '/path/to/some.iso',
                        'cpuPinning': {'0': '0', '1': '1'},
                        'devices': [{'device': 'ide', 'type': 'controller'},
                                    {'address': {'bus': '0x00',
                                                 'domain': '0x0000',
                                                 'function': '0x0',
                                                 'slot': '0x03',
                                                 'type': 'pci'},
                                     'alias': 'net0',
                                     'macAddr': '5F:45:00:95:F6:3F',
                                     'network': 'virbr0',
                                     'nicModel': 'virtio'}],
                        'display': 'vnc',
                        'drives': [{'boot': 'true',
                                    'domainID': 'domainpi',
                                    'format': 'cow',
                                    'imageID': 'imageid',
                                    'poolID': 'pooid',
                                    'volumeID': 'volumeid'}],
                        'kvmEnable': 'true',
                        'macAddr': '52:54:00:7d:96:bd',
                        'memSize': '1024',
                        'nicModel': 'virtio',
                        'smp': '2',
                        'vmId': '209b27e4-aed3-11e1-a547-00247edb4743',
                        'vmName': 'rhel62vdsm',
                        'vmType': 'kvm'}

        # test parsing only arguments
        r1 = serv.do_create(['/dev/null'] + allArgs)
        self.assertEquals(r1, expectResult)

        # test parsing only configure file
        with configFile(allArgs) as conf:
            r2 = serv.do_create([conf])
        self.assertEquals(r2, expectResult)

        # test parsing configure file and arguments
        with configFile(plainArgs) as conf:
            r3 = serv.do_create([conf] + nestArgs)
        self.assertEquals(r3, expectResult)

        # changing one argument should result a different dictionary
        allArgs[-1] = 'cpuPinning={0:1,1:0}'
        r4 = serv.do_create(['/dev/null'] + allArgs)
        self.assertNotEquals(r4, expectResult)
