#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import os
import re
import traceback

from vdsm.hook import hooking


def main():
    log_firmware = os.environ.get('log_firmware')
    hooking.log(f'log_firmware: "{log_firmware}"')
    if log_firmware == 'off':
        return

    log_firmware_vm_regexp = os.environ.get('log_firmware_vm_regexp')
    hooking.log(f'log_firmware_vm_regexp: "{log_firmware_vm_regexp}"')

    domxml = hooking.read_domxml()
    vm_name = domxml.getElementsByTagName('name')[0].firstChild.data
    hooking.log(f'vm_name: {vm_name}')

    if (
        log_firmware != 'on'
        and (
            not log_firmware_vm_regexp
            or not re.match(log_firmware_vm_regexp, vm_name)
        )
    ):
        return

    domain = domxml.getElementsByTagName('domain')[0]

    domain.setAttribute(
        'xmlns:qemu',
        'http://libvirt.org/schemas/domain/qemu/1.0'
    )

    qemucl = domxml.createElement('qemu:commandline')

    log_dir = os.environ.get(
        'log_firmware_dir',
        '/var/log/qemu-firmware'
    )
    for arg in (
        '-chardev',
        f'file,id=firmware,path={log_dir}/{vm_name}-firmware.log',
        '-device',
        'isa-debugcon,iobase=0x402,chardev=firmware',
    ):
        argelement = domxml.createElement('qemu:arg')
        argelement.setAttribute('value', arg)
        qemucl.appendChild(argelement)

    domain.appendChild(qemucl)
    hooking.log(f'qemucl: [{qemucl.toprettyxml()}]')

    hooking.log(f'domxml: [{domxml.toprettyxml()}]')
    hooking.write_domxml(domxml)


if __name__ == '__main__':
    try:
        main()
    except:
        hooking.exit_hook(
            'log_firmware: %s' % (
                traceback.format_exc()
            ),
            return_code=1
        )
