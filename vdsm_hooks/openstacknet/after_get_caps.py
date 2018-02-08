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
from __future__ import print_function

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


def _usage():
    print('Usage: %s option' % (sys.argv[0], ))
    print('\t-h, --help\t\tdisplay this help')
    print('\t-t, --test\t\trun tests')


def _test():
    print(_update_openstack_binding_host_ids({}))


def _get_openstack_binding_host_id():
    rc, out, err = hooking.execCmd(
        ['/usr/libexec/vdsm/hooks/openstacknet-get-config', 'host'],
        sudo=True, raw=True)

    if rc:
        return None

    return out.decode()


def _update_openstack_binding_host_ids(caps):
    openstack_binding_host_id = _get_openstack_binding_host_id()
    if openstack_binding_host_id is not None:
        openstack_binding_host_ids = caps.get(CAPS_BINDING_KEY, {})
        openstack_binding_host_ids[openstacknet_utils.PT_OVS] = \
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

    caps = hooking.read_json()
    caps = _update_openstack_binding_host_ids(caps)
    hooking.write_json(caps)
