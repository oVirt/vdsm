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
