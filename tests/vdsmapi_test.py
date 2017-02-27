#
# Copyright 2016-2017 Red Hat, Inc.
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

import json

from vdsm.api import vdsmapi
from yajsonrpc import JsonRpcError

from testlib import VdsmTestCase as TestCaseBase

try:
    import vdsm.gluster.apiwrapper as gapi
    _glusterEnabled = True
    gapi
except ImportError:
    _glusterEnabled = False


class SchemaWrapper(object):

    def __init__(self):
        self._schema = None
        self._events_schema = None

    def schema(self):
        if self._schema is None:
            paths = [vdsmapi.find_schema()]
            if _glusterEnabled:
                paths.append(vdsmapi.find_schema('vdsm-api-gluster'))
            self._schema = vdsmapi.Schema(paths, True)
        return self._schema

    def events_schema(self):
        if self._events_schema is None:
            path = [vdsmapi.find_schema('vdsm-events')]
            self._events_schema = vdsmapi.Schema(path, True)
        return self._events_schema


_events_schema = SchemaWrapper()
_schema = SchemaWrapper()


class DataVerificationTests(TestCaseBase):

    def test_optional_params(self):
        params = {u"addr": u"rack05-pdu01-lab4.tlv.redhat.com", u"port": 54321,
                  u"agent": u"apc_snmp", u"username": u"emesika",
                  u"password": u"pass", u"action": u"off",
                  u"options": u"port=15"}

        _schema.schema().verify_args(
            vdsmapi.MethodRep('Host', 'fenceNode'), params)

    def test_ok_response(self):
        ret = {u'power': u'on'}

        _schema.schema().verify_retval(
            vdsmapi.MethodRep('Host', 'fenceNode'), ret)

    def test_unknown_response_type(self):
        with self.assertRaises(JsonRpcError) as e:
            ret = {u'My caps': u'My capabilites'}

            _schema.schema().verify_retval(
                vdsmapi.MethodRep('Host', 'getCapabilities'), ret)

        self.assertIn('My caps', e.exception.message)

    def test_unknown_param(self):
        params = {u"storagepoolID": u"00000002-0002-0002-0002-0000000000f6",
                  u"onlyForce": True,
                  u"storagedomainID": u"773adfc7-10d4-4e60-b700-3272ee1871f9"}

        with self.assertRaises(JsonRpcError) as e:
            _schema.schema().verify_args(
                vdsmapi.MethodRep('StorageDomain', 'detach'), params)

        self.assertIn('onlyForce', e.exception.message)

    def test_wrong_param_type(self):
        params = {u"storagepoolID": u"00000000-0000-0000-0000-000000000000",
                  u"domainType": u"1",
                  u"connectionParams": [{u"timeout": 0,
                                         u"version": u"3",
                                         u"export": u"1.1.1.1:/export/ovirt",
                                         u"retrans": 1}]}

        with self.assertRaises(JsonRpcError) as e:
            _schema.schema().verify_args(
                vdsmapi.MethodRep('StoragePool', 'disconnectStorageServer'),
                params)

        self.assertIn('StorageDomainType', e.exception.message)

    def test_list_ret(self):
        ret = [{u"status": 0, u"id": u"f6de012c-be35-47cb-94fb-f01074a5f9ef"}]

        _schema.schema().verify_retval(
            vdsmapi.MethodRep('StoragePool', 'disconnectStorageServer'), ret)

    def test_complex_ret_type(self):
        ret = {u"cpuStatistics": {u"1": {u"cpuUser": u"1.47",
                                         u"nodeIndex": 0,
                                         u"cpuSys": u"1.20",
                                         u"cpuIdle": u"97.33"},
                                  u"0": {u"cpuUser": u"0.33",
                                         u"nodeIndex": 0,
                                         u"cpuSys": u"0.33",
                                         u"cpuIdle": u"99.34"},
                                  u"3": {u"cpuUser": u"0.47",
                                         u"nodeIndex": 0,
                                         u"cpuSys": u"0.27",
                                         u"cpuIdle": u"99.26"},
                                  u"2": {u"cpuUser": u"0.33",
                                         u"nodeIndex": 0,
                                         u"cpuSys": u"0.27",
                                         u"cpuIdle": u"99.40"},
                                  u"5": {u"cpuUser": u"0.20",
                                         u"nodeIndex": 0,
                                         u"cpuSys": u"0.33",
                                         u"cpuIdle": u"99.47"},
                                  u"4": {u"cpuUser": u"0.47",
                                         u"nodeIndex": 0,
                                         u"cpuSys": u"0.27",
                                         u"cpuIdle": u"99.26"},
                                  u"7": {u"cpuUser": u"0.60",
                                         u"nodeIndex": 0,
                                         u"cpuSys": u"0.40",
                                         u"cpuIdle": u"99.00"},
                                  u"6": {u"cpuUser": u"0.47",
                                         u"nodeIndex": 0,
                                         u"cpuSys": u"0.40",
                                         u"cpuIdle": u"99.13"}},
               u"numaNodeMemFree": {u"0": {u"memPercent": 15,
                                           u"memFree": u"13645"}},
               u"memShared": 0,
               u"thpState": u"madvise",
               u"vmCount": 0,
               u"memUsed": u"3",
               u"storageDomains": {},
               u"incomingVmMigrations": 0,
               u"network": {u"bond0": {u"rxErrors": u"0",
                                       u"txErrors": u"0",
                                       u"speed": u"1000",
                                       u"rxDropped": u"0",
                                       u"name": u"bond0",
                                       u"tx": u"0",
                                       u"txDropped": u"0",
                                       u"sampleTime": 1456911173.218806,
                                       u"rx": u"0",
                                       u"state": u"down"},
                            u"ovirtmgmt": {u"rxErrors": u"0",
                                           u"txErrors": u"0",
                                           u"speed": u"1000",
                                           u"rxDropped": u"0",
                                           u"name": u"ovirtmgmt",
                                           u"tx": u"560936",
                                           u"txDropped": u"0",
                                           u"sampleTime": 1456911173.21,
                                           u"rx": u"2106573",
                                           u"state": u"up"},
                            u"lo": {u"rxErrors": u"0",
                                    u"txErrors": u"0",
                                    u"speed": u"1000",
                                    u"rxDropped": u"0",
                                    u"name": u"lo",
                                    u"tx": u"2308049",
                                    u"txDropped": u"0",
                                    u"sampleTime": 1456911173.218806,
                                    u"rx": u"2308049",
                                    u"state": u"up"},
                            u";vdsmdummy;": {u"rxErrors": u"0",
                                             u"txErrors": u"0",
                                             u"speed": u"1000",
                                             u"rxDropped": u"0",
                                             u"name": u";vdsmdummy;",
                                             u"tx": u"0",
                                             u"txDropped": u"0",
                                             u"sampleTime": 145691117.2,
                                             u"rx": u"0",
                                             u"state": u"down"},
                            u"em1": {u"rxErrors": u"0",
                                     u"txErrors": u"0",
                                     u"speed": u"1000",
                                     u"rxDropped": u"0",
                                     u"name": u"em1",
                                     u"tx": u"580586",
                                     u"txDropped": u"0",
                                     u"sampleTime": 1456911173.218806,
                                     u"rx": u"2310757",
                                     u"state": u"up"},
                            u"wlp1s2": {u"rxErrors": u"0",
                                        u"txErrors": u"0",
                                        u"speed": u"1000",
                                        u"rxDropped": u"0",
                                        u"name": u"wlp1s2",
                                        u"tx": u"0",
                                        u"txDropped": u"0",
                                        u"sampleTime": 1456911173.21880,
                                        u"rx": u"0",
                                        u"state": u"down"}},
               u"txDropped": u"0",
               u"cpuUser": u"0.54",
               u"ksmPages": 100,
               u"elapsedTime": u"106",
               u"cpuLoad": u"0.42",
               u"cpuSys": u"0.43",
               u"diskStats": {u"/var/log": {u"free": u"10810"},
                              u"/var/log/core": {u"free": u"10810"},
                              u"/var/run/vdsm/": {u"free": u"7966"},
                              u"/tmp": {u"free": u"7967"}},
               u"cpuUserVdsmd": u"1.07",
               u"netConfigDirty": u"False",
               u"memCommitted": 0,
               u"ksmState": False,
               u"vmMigrating": 0,
               u"ksmMergeAcrossNodes": True,
               u"ksmCpu": 0,
               u"memAvailable": 15226,
               u"bootTime": u"1456910791",
               u"haStats": {u"active": False,
                            u"configured": False,
                            u"score": 0,
                            u"localMaintenance": False,
                            u"globalMaintenance": False},
               u"momStatus": u"active",
               u"rxDropped": u"0",
               u"outgoingVmMigrations": 0,
               u"swapTotal": 8007,
               u"swapFree": 8007,
               u"dateTime": u"2016-03-02T09:32:54 GMT",
               u"anonHugePages": u"0",
               u"memFree": 15482,
               u"cpuIdle": u"99.03",
               u"vmActive": 0,
               u"v2vJobs": {},
               u"cpuSysVdsmd": u"0.53"}

        _schema.schema().verify_retval(
            vdsmapi.MethodRep('Host', 'getStats'), ret)

    def test_badly_defined_ret_type(self):
        ret = {u'pci_0000_00_1b_0':
               {u'params':
                {u'product': u'7 Series/C210 Series Chipset Family ',
                 u'vendor': u'Intel Corporation',
                 u'product_id': u'0x1e20',
                 u'parent': u'computer',
                 u'vendor_id': u'0x8086',
                 u'capability': u'pci',
                 u'address': {u'slot': u'27',
                              u'bus': u'0',
                              u'domain': u'0',
                              u'function': u'0'}}}}

        # type definition is broken for this verb
        with self.assertRaises(JsonRpcError) as e:
            _schema.schema().verify_retval(
                vdsmapi.MethodRep('Host', 'hostdevListByCaps'), ret)

        self.assertIn('is not a list', e.exception.message)

    def test_allvmstats(self):
        ret = [{'vcpuCount': '1',
                'displayInfo': [{'tlsPort': u'5900',
                                 'ipAddress': '0',
                                 'type': u'spice',
                                 'port': '-1'}],
                'hash': '-3472228600028768455',
                'acpiEnable': u'true',
                'displayIp': '0',
                'guestFQDN': '',
                'vmId': u'f1eb5cc5-d793-46c6-b1e3-719345bfec0c',
                'pid': '32632',
                'cpuUsage': '2660000000',
                'timeOffset': u'0',
                'vNodeRuntimeInfo': {'0': [0]},
                'session': 'Unknown',
                'displaySecurePort': u'5900',
                'displayPort': '-1',
                'memUsage': '0',
                'guestIPs': '',
                'pauseCode': 'NOERR',
                'vcpuQuota': '-1',
                'username': 'Unknown',
                'kvmEnable': u'true',
                'network': {u'vnet0': {'macAddr': u'00:1a:4a:16:01:51',
                                       'rxDropped': '1572',
                                       'tx': '0',
                                       'rxErrors': '0',
                                       'txDropped': '0',
                                       'rx': '90',
                                       'txErrors': '0',
                                       'state': 'unknown',
                                       'sampleTime': 4319358.22,
                                       'speed': '1000',
                                       'name': u'vnet0'}},
                'displayType': 'qxl',
                'cpuUser': '0.57',
                'vmJobs': {},
                'disks': {
                    u'vdq': {'readLatency': '0',
                             'writtenBytes': '0',
                             'writeOps': '0',
                             'apparentsize': '1073741824',
                             'readOps': '0',
                             'writeLatency': '0',
                             'imageID': u'95c06337-8c23-4dfb-b0bf-a5f30bc9d33',
                             'readBytes': '0',
                             'flushLatency': '0',
                             'readRate': '0.0',
                             'truesize': '0',
                             'writeRate': '0.0'},
                    u'vdp': {'readLatency': '0',
                             'writtenBytes': '0',
                             'writeOps': '0',
                             'apparentsize': '1073741824',
                             'readOps': '0',
                             'writeLatency': '0',
                             'imageID': u'702df0bd-fff6-41eb-817b-103b23e5bd9',
                             'readBytes': '0',
                             'flushLatency': '0',
                             'readRate': '0.0',
                             'truesize': '0',
                             'writeRate': '0.0'}},
                'monitorResponse': '0',
                'elapsedTime': '2560',
                'vmType': u'kvm',
                'cpuSys': '0.20',
                'status': 'Up',
                'guestCPUCount': -1,
                'appsList': (),
                'clientIp': '',
                'statusTime': '4319358220',
                'vmName': u'vm1',
                'vcpuPeriod': 100000},
               {'vcpuCount': '1',
                'displayInfo': [{'tlsPort': u'5901',
                                 'ipAddress': '0',
                                 'type': u'spice',
                                 'port': '-1'}],
                'hash': '8478318448907411309',
                'acpiEnable': u'true',
                'displayIp': '0',
                'guestFQDN': '',
                'vmId': u'7d3efc8f-405e-40cc-b512-1f8de3d6d587',
                'pid': '32734',
                'cpuUsage': '1220000000',
                'timeOffset': u'0',
                'vNodeRuntimeInfo': {'0': [0]},
                'session': 'Unknown',
                'displaySecurePort': u'5901',
                'displayPort': '-1',
                'memUsage': '0',
                'guestIPs': '',
                'pauseCode': 'NOERR',
                'vcpuQuota': '-1',
                'username': 'Unknown',
                'kvmEnable': u'true',
                'network': {u'vnet1': {'macAddr': u'00:1a:4a:16:01:52',
                                       'rxDropped': '0',
                                       'tx': '7478',
                                       'rxErrors': '0',
                                       'txDropped': '0',
                                       'rx': '331023',
                                       'txErrors': '0',
                                       'state': 'unknown',
                                       'sampleTime': 4319358.22,
                                       'speed': '1000',
                                       'name': u'vnet1'}},
                'displayType': 'qxl',
                'cpuUser': '0.34',
                'vmJobs': {},
                'disks': {
                    u'vda': {'readLatency': '0',
                             'writtenBytes': '219136',
                             'writeOps': '81',
                             'apparentsize': '2621440',
                             'readOps': '791',
                             'writeLatency': '0',
                             'imageID': u'e2461e60-ee91-4500-bebf-f50f2a2f644',
                             'readBytes': '15910400',
                             'flushLatency': '0',
                             'readRate': '0.0',
                             'truesize': '2564096',
                             'writeRate': '0.0'},
                    u'hdc': {'readLatency': '0',
                             'writtenBytes': '0',
                             'writeOps': '0',
                             'apparentsize': '0',
                             'readOps': '1',
                             'writeLatency': '0',
                             'readBytes': '30',
                             'flushLatency': '0',
                             'readRate': '0.0',
                             'truesize': '0',
                             'writeRate': '0.0'}},
                'monitorResponse': '0',
                'elapsedTime': '2541',
                'vmType': u'kvm',
                'cpuSys': '0.07',
                'status': 'Up',
                'guestCPUCount': -1,
                'appsList': (),
                'clientIp': '',
                'statusTime': '4319358220',
                'vmName': u'vm2',
                'vcpuPeriod': 100000}]

        _schema.schema().verify_retval(
            vdsmapi.MethodRep('Host', 'getAllVmStats'), ret)

    def test_missing_method(self):
        with self.assertRaises(vdsmapi.MethodNotFound):
            _schema.schema().get_method(
                vdsmapi.MethodRep('missing_class', 'missing_method'))

    def test_missing_type(self):
        with self.assertRaises(vdsmapi.TypeNotFound):
            _schema.schema().get_type('Missing_type')

    def test_events_params(self):
        params = {u"notify_time": 4303947020,
                  u"426aef82-ea1d-4442-91d3-fd876540e0f0":
                      {u"status": u"Up",
                       u"displayInfo": [{u"tlsPort": u"5901",
                                         u"ipAddress": u"0",
                                         u"type": u"spice",
                                         u"port": u"5900"}],
                       u"hash": u"880508647164395013",
                       u"cpuUser": u"0.00",
                       u"displayIp": u"0",
                       u"monitorResponse": u"0",
                       u"elapsedTime": u"110",
                       u"displayType": u"qxl",
                       u"cpuSys": u"0.00",
                       u"pauseCode": u"NOERR",
                       u"displayPort": u"5900",
                       u"displaySecurePort": u"5901",
                       u"timeOffset": u"0",
                       u"clientIp": u"",
                       u"vcpuQuota": u"-1",
                       u"vcpuPeriod": 100000}}
        sub_id = '|virt|VM_status|426aef82-ea1d-4442-91d3-fd876540e0f0'

        _events_schema.events_schema().verify_event_params(sub_id, params)

    def test_create_complex_params(self):
        complex_type = {'lease': {'sd_id': 'UUID', 'lease_id': 'UUID'}}
        self.assertEqual(
            _schema.schema().get_args_dict('Lease', 'create'),
            json.dumps(complex_type, indent=4))

    def test_no_params(self):
        self.assertEqual(_schema.schema().get_args_dict(
            'Host', 'getCapabilities'), None)

    def test_single_param(self):
        complex_type = {'vmID': {'UUID': 'UUID'}}
        self.assertEqual(_schema.schema().get_args_dict(
            'VM', 'getStats'), json.dumps(complex_type, indent=4))
