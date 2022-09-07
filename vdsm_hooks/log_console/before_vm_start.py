#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
