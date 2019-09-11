#!/usr/bin/python2
#
# Copyright 2018 Red Hat, Inc.
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
from __future__ import print_function

import errno
import getopt
import sys

try:
    import hooking
except ImportError:
    print(
        'please run with PYTHONPATH='
        '/usr/lib/python2.7/site-packages/vdsm/hook %s -t' % sys.argv[0])
    sys.exit(1)

import openstacknet_utils

CAPS_BINDING_KEY = 'openstack_binding_host_ids'
OVS_CTL = '/usr/share/openvswitch/scripts/ovs-ctl'
OVS_VSCTL = '/usr/bin/ovs-vsctl'


def _usage():
    print('Usage: %s option' % (sys.argv[0], ))
    print('\t-h, --help\t\tdisplay this help')
    print('\t-t, --test\t\trun tests')


def _test():
    print(_update_openstack_binding_host_ids({}))


def _get_openstack_binding_host_id():
    return (_get_open_vswitch_odl_os_hostconfig_hostid() or
            _get_open_vswitch_hostname())


def _get_open_vswitch_odl_os_hostconfig_hostid():
    """
    Returns the external_ids:odl_os_hostconfig_hostid of the table
    Open_vSwitch from Open vSwitch's database. This value is used by the
    OpenStack agent of neutron's ODL plugin to identify the host.
    """
    rc, out, err = _get_ovs_external_id('odl_os_hostconfig_hostid')
    if rc == 0:
        return out[0].decode('utf-8').replace('"', '')

    return None


def _get_ovs_external_id(key):
    cmd_line = [
        OVS_VSCTL,
        '--no-wait',
        '--verbose=db_ctl_base:syslog:off',
        'get',
        'Open_vSwitch',
        '.',
        'external_ids:{}'.format(key)
    ]
    return hooking.execCmd(cmd_line, sudo=True, raw=False)


def _get_open_vswitch_hostname():
    """
    Returns the external_ids:hostname of the table Open_vSwitch from
    Open vSwitch's database. This value is used by the OpenStack agents
    of neutron's OVS and OVN plugin to identify the host.
    """
    rc, out, err = _get_ovs_external_id('hostname')
    if rc == 0:
        return out[0].decode('utf-8').replace('"', '')

    hooking.log('Failed to get Open vSwitch hostname. err = %s' % (err))
    return None


def _is_ovs_service_running():
    try:
        rc, _, _ = hooking.execCmd([OVS_CTL, 'status'])
    except OSError as err:
        # Silently ignore the missing file and consider the service as down.
        if err.errno == errno.ENOENT:
            rc = errno.ENOENT
        else:
            raise
    return rc == 0


def _update_openstack_binding_host_ids(caps):
    openstack_binding_host_id = _get_openstack_binding_host_id()
    if openstack_binding_host_id is not None:
        openstack_binding_host_ids = caps.get(CAPS_BINDING_KEY, {})
        openstack_binding_host_ids[openstacknet_utils.PT_OVS] = \
            openstack_binding_host_id
        openstack_binding_host_ids[openstacknet_utils.PT_OPENSTACK_OVN] = \
            openstack_binding_host_id
        caps[CAPS_BINDING_KEY] = openstack_binding_host_ids
    return caps


if __name__ == '__main__':
    try:
        opts, args = getopt.getopt(sys.argv[1:], 'ht', ['help', 'test'])
    except getopt.GetoptError as err:
        print(str(err))
        _usage()
        sys.exit(1)

    for option, _ in opts:
        if option in ('-h', '--help'):
            _usage()
            sys.exit()
        elif option in ('-t', '--test'):
            _test()
            sys.exit()

    if not _is_ovs_service_running():
        hooking.exit_hook('OVS is not running', return_code=0)

    caps = hooking.read_json()
    caps = _update_openstack_binding_host_ids(caps)
    hooking.write_json(caps)
