#!/usr/bin/python3
#
# Copyright 2022 Red Hat, Inc.
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

import os
import re
import traceback

from vdsm.hook import hooking


def main():
    log_console = os.environ.get('log_console')
    hooking.log(f'log_console: "{log_console}"')
    if log_console == 'off':
        return

    log_console_vm_regexp = os.environ.get('log_console_vm_regexp')
    hooking.log(f'log_console_vm_regexp: "{log_console_vm_regexp}"')

    domxml = hooking.read_domxml()
    vm_name = domxml.getElementsByTagName('name')[0].firstChild.data
    hooking.log(f'vm_name: {vm_name}')

    if (
        log_console != 'on'
        and (
            not log_console_vm_regexp
            or not re.match(log_console_vm_regexp, vm_name)
        )
    ):
        return

    devices = domxml.getElementsByTagName('devices')
    serial_devices = [
        child
        for child in devices[0].childNodes
        if child.localName == 'serial'
    ]
    hooking.log(f'serial_devices: {serial_devices}')
    if not serial_devices:
        return

    serial0 = serial_devices[0]
    hooking.log(f'before: [{serial0.toprettyxml()}]')

    log = domxml.createElement('log')
    log.setAttribute(
        'file',
        f'/var/log/libvirt/qemu/{vm_name}-console.log'
    )
    log.setAttribute('append', 'on')

    serial0.appendChild(log)
    hooking.log(f'after: [{serial0.toprettyxml()}]')

    hooking.write_domxml(domxml)


if __name__ == '__main__':
    try:
        main()
    except:
        hooking.exit_hook(
            'log_console: %s' % (
                traceback.format_exc()
            ),
            return_code=1
        )
