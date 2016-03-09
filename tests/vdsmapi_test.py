#
# Copyright 2016 Red Hat, Inc.
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

from api import vdsmapi
from yajsonrpc import JsonRpcError

from monkeypatch import MonkeyPatch
from testlib import VdsmTestCase as TestCaseBase, make_config

try:
    import gluster.apiwrapper as gapi
    _glusterEnabled = True
    gapi
except ImportError:
    _glusterEnabled = False


def api_strict_mode():
        return MonkeyPatch(vdsmapi, 'config', make_config(
            [('devel', 'api_strict_mode', 'true')]))


class SchemaWrapper(object):

    def __init__(self):
        self._schema = None

    def schema(self):
        if self._schema is None:
            paths = [vdsmapi.find_schema()]
            if _glusterEnabled:
                paths.append(vdsmapi.find_schema('vdsm-api-gluster'))
            self._schema = vdsmapi.Schema(paths)
        return self._schema

    def _name_args(self, args, kwargs, arglist):
        kwargs = kwargs.copy()
        for i, arg in enumerate(args):
            argName = arglist[i]
            kwargs[argName] = arg

        return kwargs


_schema = SchemaWrapper()


class DataVerificationTests(TestCaseBase):

    @api_strict_mode()
    def test_optional_params(self):
        params = {u"addr": u"rack05-pdu01-lab4.tlv.redhat.com", u"port": 54321,
                  u"agent": u"apc_snmp", u"username": u"emesika",
                  u"password": u"pass", u"action": u"off",
                  u"options": u"port=15"}

        _schema.schema().verify_args('Host', 'fenceNode', params)

    @api_strict_mode()
    def test_ok_response(self):
        ret = {u'power': u'on'}

        _schema.schema().verify_retval('Host', 'fenceNode', ret)

    @api_strict_mode()
    def test_unknown_response_type(self):
        with self.assertRaises(JsonRpcError) as e:
            ret = {u'My caps': u'My capabilites'}

            _schema.schema().verify_retval('Host', 'getCapabilities', ret)

        self.assertIn('My caps', e.exception.message)

    @api_strict_mode()
    def test_unknown_param(self):
        params = {u"storagepoolID": u"00000002-0002-0002-0002-0000000000f6",
                  u"onlyForce": True,
                  u"storagedomainID": u"773adfc7-10d4-4e60-b700-3272ee1871f9"}

        with self.assertRaises(JsonRpcError) as e:
            _schema.schema().verify_args('StorageDomain', 'detach', params)

        self.assertIn('onlyForce', e.exception.message)

    @api_strict_mode()
    def test_wrong_param_type(self):
        params = {u"storagepoolID": u"00000000-0000-0000-0000-000000000000",
                  u"domainType": u"1",
                  u"connectionParams": [{u"timeout": 0,
                                         u"version": u"3",
                                         u"export": u"1.1.1.1:/export/ovirt",
                                         u"retrans": 1}]}

        with self.assertRaises(JsonRpcError) as e:
            _schema.schema().verify_args('StoragePool',
                                         'disconnectStorageServer',
                                         params)

        self.assertIn('StorageDomainType', e.exception.message)

    @api_strict_mode()
    def test_list_ret(self):
        ret = [{u"status": 0, u"id": u"f6de012c-be35-47cb-94fb-f01074a5f9ef"}]

        _schema.schema().verify_retval('StoragePool',
                                       'disconnectStorageServer', ret)

    @api_strict_mode()
    def test_complex_ret_type(self):
        ret = {u"cpuStatistics": {u"1": {u"cpuUser": 1.47,
                                         u"numaNodeIndex": 0,
                                         u"cpuSys": 1.20,
                                         u"cpuIdle": 97.33},
                                  u"0": {u"cpuUser": 0.33,
                                         u"numaNodeIndex": 0,
                                         u"cpuSys": 0.33,
                                         u"cpuIdle": 99.34},
                                  u"3": {u"cpuUser": 0.47,
                                         u"numaNodeIndex": 0,
                                         u"cpuSys": 0.27,
                                         u"cpuIdle": 99.26},
                                  u"2": {u"cpuUser": 0.33,
                                         u"numaNodeIndex": 0,
                                         u"cpuSys": 0.27,
                                         u"cpuIdle": 99.40},
                                  u"5": {u"cpuUser": 0.20,
                                         u"numaNodeIndex": 0,
                                         u"cpuSys": 0.33,
                                         u"cpuIdle": 99.47},
                                  u"4": {u"cpuUser": 0.47,
                                         u"numaNodeIndex": 0,
                                         u"cpuSys": 0.27,
                                         u"cpuIdle": 99.26},
                                  u"7": {u"cpuUser": 0.60,
                                         u"numaNodeIndex": 0,
                                         u"cpuSys": 0.40,
                                         u"cpuIdle": 99.00},
                                  u"6": {u"cpuUser": 0.47,
                                         u"numaNodeIndex": 0,
                                         u"cpuSys": 0.40,
                                         u"cpuIdle": 99.13}},
               u"numaNodeMemFree": {u"0": {u"memPercent": 15,
                                           u"memFree": 13645}},
               u"memShared": 0,
               u"thpState": u"madvise",
               u"rxRate": 0.02,
               u"vmCount": 0,
               u"memUsed": 3,
               u"storageDomains": {},
               u"incomingVmMigrations": 0,
               u"network": {u"bond0": {u"rxErrors": 0,
                                       u"txRate": 0.0,
                                       u"rxRate": 0.0,
                                       u"txErrors": 0,
                                       u"speed": 1000,
                                       u"rxDropped": 0,
                                       u"name": u"bond0",
                                       u"tx": 0,
                                       u"txDropped": 0,
                                       u"sampleTime": 1456911173.218806,
                                       u"rx": 0,
                                       u"state": u"down"},
                            u"ovirtmgmt": {u"rxErrors": 0,
                                           u"txRate": 0.0,
                                           u"rxRate": 0.0,
                                           u"txErrors": 0,
                                           u"speed": 1000,
                                           u"rxDropped": 0,
                                           u"name": u"ovirtmgmt",
                                           u"tx": 560936,
                                           u"txDropped": 0,
                                           u"sampleTime": 1456911173.21,
                                           u"rx": 2106573,
                                           u"state": u"up"},
                            u"lo": {u"rxErrors": 0,
                                    u"txRate": 0.1,
                                    u"rxRate": 0.1,
                                    u"txErrors": 0,
                                    u"speed": 1000,
                                    u"rxDropped": 0,
                                    u"name": u"lo",
                                    u"tx": 2308049,
                                    u"txDropped": 0,
                                    u"sampleTime": 1456911173.218806,
                                    u"rx": 2308049,
                                    u"state": u"up"},
                            u";vdsmdummy;": {u"rxErrors": 0,
                                             u"txRate": 0.0,
                                             u"rxRate": 0.0,
                                             u"txErrors": 0,
                                             u"speed": 1000,
                                             u"rxDropped": 0,
                                             u"name": u";vdsmdummy;",
                                             u"tx": 0,
                                             u"txDropped": 0,
                                             u"sampleTime": 145691117.2,
                                             u"rx": 0,
                                             u"state": u"down"},
                            u"em1": {u"rxErrors": 0,
                                     u"txRate": 0.0,
                                     u"rxRate": 0.0,
                                     u"txErrors": 0,
                                     u"speed": 1000,
                                     u"rxDropped": 0,
                                     u"name": u"em1",
                                     u"tx": 580586,
                                     u"txDropped": 0,
                                     u"sampleTime": 1456911173.218806,
                                     u"rx": 2310757,
                                     u"state": u"up"},
                            u"wlp1s2": {u"rxErrors": 0,
                                        u"txRate": 0.0,
                                        u"rxRate": 0.0,
                                        u"txErrors": 0,
                                        u"speed": 1000,
                                        u"rxDropped": 0,
                                        u"name": u"wlp1s2",
                                        u"tx": 0,
                                        u"txDropped": 0,
                                        u"sampleTime": 1456911173.21880,
                                        u"rx": 0,
                                        u"state": u"down"}},
               u"txDropped": 0,
               u"cpuUser": 0.54,
               u"ksmPages": 100,
               u"elapsedTime": 106,
               u"cpuLoad": 0.42,
               u"cpuSys": 0.43,
               u"diskStats": {u"/var/log": {u"free": 10810},
                              u"/var/log/core": {u"free": 10810},
                              u"/var/run/vdsm/": {u"free": 7966},
                              u"/tmp": {u"free": 7967}},
               u"cpuUserVdsmd": 1.07,
               u"netConfigDirty": False,
               u"memCommitted": 0,
               u"ksmState": False,
               u"vmMigrating": 0,
               u"ksmCpu": 0.0,
               u"memAvailable": 15226,
               u"txRate": 0.02,
               u"bootTime": 1456910791,
               u"haStatus": {u"active": False,
                             u"configured": False,
                             u"score": 0,
                             u"localMaintenance": False,
                             u"globalMaintenance": False},
               u"momStatus": u"active",
               u"rxDropped": 0,
               u"outgoingVmMigrations": 0,
               u"swapTotal": 8007,
               u"swapFree": 8007,
               u"dateTime": u"2016-03-02T09:32:54 GMT",
               u"anonHugePages": 0,
               u"memFree": 15482,
               u"cpuIdle": 99.03,
               u"vmActive": 0,
               u"v2vJobs": {},
               u"cpuSysVdsmd": 0.53}

        _schema.schema().verify_retval('Host', 'getStats', ret)

    @api_strict_mode()
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
            _schema.schema().verify_retval('Host',
                                           'hostdevListByCaps', ret)

        self.assertIn('is not a list', e.exception.message)

    def test_missing_method(self):
        with self.assertRaises(vdsmapi.MethodNotFound):
            _schema.schema().get_method('Missing_class', 'missing_method')

    def test_missing_type(self):
        with self.assertRaises(vdsmapi.TypeNotFound):
            _schema.schema().get_type('Missing_type')
