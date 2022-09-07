#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

'''
OpenStack Network Hook (Post vm start)
=============================================
The hook unpauses a vm if it was started in the paused state.

Syntax:
    { 'vmId': 'VM_ID', 'vnic_id': 'port_id' }
Where:
    VM_ID should be replaced with the vm id.'''

from __future__ import absolute_import

import libvirt
import os
import time
import traceback
import hooking
from openstacknet_utils import MARK_FOR_UNPAUSE_PATH
from openstacknet_utils import VM_ID_KEY

from vdsm import client
from vdsm.config import config
from vdsm import utils


OPENSTACK_NIC_WAIT_TIME = 15


def resume_paused_vm(vm_id):
    unpause_file = MARK_FOR_UNPAUSE_PATH % vm_id
    if os.path.isfile(unpause_file):
        use_tls = config.getboolean('vars', 'ssl')
        cli = client.connect('localhost', use_tls=use_tls)
        with utils.closing(cli):
            cli.VM.cont(vmID=vm_id)
        os.remove(unpause_file)


def main():

    # TODO (HACK):
    # This code waits for the nic to be attached to neutron for a
    # certain amount of time. This is one way of going around the
    # race between the code and the vm nic becoming active. It is
    # a very fragile hack, as there is no guarantee the nic will
    # actually be ready after this.
    vm_id = os.environ[VM_ID_KEY]
    launch_flags = hooking.load_vm_launch_flags_from_file(vm_id)
    if launch_flags == libvirt.VIR_DOMAIN_START_PAUSED:
        time.sleep(OPENSTACK_NIC_WAIT_TIME)
        resume_paused_vm(vm_id)


if __name__ == '__main__':
    try:
        main()
    except:
        hooking.exit_hook('openstacknet hook: [unexpected error]: %s\n' %
                          traceback.format_exc())
