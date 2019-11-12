#!/usr/bin/python3

from __future__ import absolute_import
from __future__ import print_function

import os
import hooking
import sys
import traceback
from six.moves import urllib
from xml.dom import minidom


'''
httpsisoboot hook:
    Let the VM boot from an ISO image made available via an https URL without
    the need to import the ISO into an ISO storage domain.
    No support for plain http
syntax:
    httpsisoboot=https://server/path/to/disk.iso
'''


_DISK_BY_INDEX = {
    0: 'hda',
    1: 'hdb',
    2: 'hdc',
    3: 'hdd'
}


def pretty_print(data):
    return '\n'.join(
        [
            line
            for line
            in data.toprettyxml(
                indent=' ' * 2,
                encoding='UTF-8',
            ).split('\n')
            if line.strip()
        ]
    )


def increase_devices_boot_order(devices):
    xmldevices = [
        e for e in devices.childNodes
        if e.nodeType == e.ELEMENT_NODE
    ]
    for d in xmldevices:
        boots = d.getElementsByTagName('boot')
        for boot in boots:
            if boot.hasAttribute('order'):
                try:
                    boot.setAttribute(
                        'order',
                        str(
                            int(
                                boot.getAttribute('order')
                            ) + 1
                        )
                    )
                except ValueError:
                    hooking.log(
                        'httpsisoboot: unable to manipulate the boot order: ' +
                        d.toxml()
                    )
                    raise


def create_https_iso_element(domxml, protocol, hostname, port, url_path):
    '''
    <disk type='network' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source protocol="https" name="url_path">
        <host name="hostname" port="80"/>
      </source>
      <target dev='hdc' bus='ide' tray='closed'/>
      <readonly/>
      <boot order='1'/>
    </disk>
    '''

    disk = domxml.createElement('disk')
    disk.setAttribute('type', 'network')
    disk.setAttribute('device', 'cdrom')

    driver = domxml.createElement('driver')
    driver.setAttribute('name', 'qemu')
    driver.setAttribute('type', 'raw')
    disk.appendChild(driver)

    source = domxml.createElement('source')
    source.setAttribute('protocol', protocol)
    source.setAttribute('name', url_path)

    host = domxml.createElement('host')
    host.setAttribute('name', hostname)
    host.setAttribute('port', port)
    source.appendChild(host)
    disk.appendChild(source)

    readonly = domxml.createElement('readonly')
    disk.appendChild(readonly)

    # find a name for hdX
    target = domxml.createElement('target')
    target.setAttribute('bus', 'ide')
    target.setAttribute('tray', 'closed')
    xmldisks = domxml.getElementsByTagName('disk')
    disks = []
    for d in xmldisks:
        disks.append(d.getElementsByTagName('target')[0].getAttribute('dev'))
    found = False
    for i in range(0, 4):
        dname = _DISK_BY_INDEX.get(i)
        if dname and dname not in disks:
            target.setAttribute('dev', dname)
            found = True
            break
    if not found:
        hooking.exit_hook('httpsisoboot: unable to attach another ide cdrom\n')
    disk.appendChild(target)

    boot = domxml.createElement('boot')
    boot.setAttribute('order', '1')
    disk.appendChild(boot)

    return disk


def validate_URL(url):
    parsed = urllib.parse.urlsplit(url)
    protocol = parsed.scheme
    if protocol != 'https':
        hooking.exit_hook(
            (
                "httpsisoboot: '{protocol}' is not supported, "
                "please use https\n"
            ).format(protocol=protocol)
        )

    hostname = parsed.netloc.split(':')[0]
    if not hostname:
        hooking.exit_hook(
            (
                "httpsisoboot: invalid hostname in the URL '{url}'\n"
            ).format(url=url)
        )

    port = 443  # https only
    if parsed.port is not None:
        port = parsed.port
    if not port >= 1 and port <= 65535:
        hooking.exit_hook(
            (
                "httpsisoboot: invalid port in the URL '{url}'\n"
            ).format(url=url)
        )
    port_s = str(port)

    url_path = parsed.path
    if parsed.query:
        url_path += '?' + parsed.query

    return protocol, hostname, port_s, url_path


def main():
    if 'httpsisoboot' in os.environ:
        httpsisoboot = os.environ['httpsisoboot']
        protocol, hostname, port_s, url_path = validate_URL(httpsisoboot)

        domxml = hooking.read_domxml()
        devices = domxml.getElementsByTagName('devices')[0]
        increase_devices_boot_order(devices)

        diskdev = create_https_iso_element(
            domxml,
            protocol,
            hostname,
            port_s,
            url_path
        )
        devices.appendChild(diskdev)
        hooking.write_domxml(domxml)


def test():
    text = '''
      <devices>
        <emulator>/usr/bin/qemu-kvm</emulator>
        <disk type='file' device='cdrom'>
          <driver name='qemu' type='raw'/>
          <source file='/rhev/data-center/_test.iso' startupPolicy='optional'/>
          <backingStore/>
          <target dev='hdc' bus='ide'/>
          <readonly/>
          <serial></serial>
          <alias name='ide0-1-0'/>
          <address type='drive' controller='0' bus='1' target='0' unit='0'/>
        </disk>
        <disk type='file' device='disk' snapshot='no'>
          <driver name='qemu' type='raw' cache='none' error_policy='stop'/>
          <source file='/rhev/data-center/test_d0'/>
          <backingStore/>
          <target dev='vda' bus='virtio'/>
          <serial>6ad5f395-d4f6-419f-a232-bc286bafca97</serial>
          <boot order='2'/>
          <alias name='virtio-disk0'/>
        </disk>
        <disk type='file' device='disk' snapshot='no'>
          <driver name='qemu' type='raw' cache='none' error_policy='stop'/>
          <source file='/rhev/data-center/test_d1'/>
          <backingStore/>
          <target dev='vdb' bus='virtio'/>
          <serial>7b11ff9a-3dbe-4260-8036-253e4efcd59b</serial>
          <alias name='virtio-disk1'/>
        </disk>
        <interface type='bridge'>
          <mac address='00:1a:4a:4f:bd:39'/>
          <source bridge='ovirtmgmt'/>
          <bandwidth>
          </bandwidth>
          <target dev='vnet2'/>
          <model type='virtio'/>
          <filterref filter='vdsm-no-mac-spoofing'/>
          <link state='up'/>
          <boot order='1'/>
          <alias name='net0'/>
        </interface>
      </devices>
    '''
    url = 'https://server.example.com:8080/path/to/disk.iso'
    domxml = minidom.parseString(text)
    devices = domxml.getElementsByTagName('devices')[0]
    print(
        "\n Devices definition before increase_devices_boot_order \n %s"
        % pretty_print(devices)
    )
    increase_devices_boot_order(devices)
    print(
        "\n Devices definition after increase_devices_boot_order \n %s"
        % pretty_print(devices)
    )
    protocol, hostname, port_s, url_path = validate_URL(url)
    print(
        (
            "\n Validated URL \n"
            " protocol: '{protocol}'\n"
            " hostname: '{hostname}'\n"
            " port: '{port_s}'\n"
            " path: '{url_path}'\n"
        ).format(
            protocol=protocol,
            hostname=hostname,
            port_s=port_s,
            url_path=url_path,
        )
    )
    diskdev = create_https_iso_element(
        domxml,
        protocol,
        hostname,
        port_s,
        url_path
    )
    devices.appendChild(diskdev)
    print(
        "\n Devices definition after setting httpsIsoElement \n %s"
        % pretty_print(devices)
    )


if __name__ == '__main__':
    try:
        if '--test' in sys.argv:
            test()
        else:
            main()
    except:
        hooking.exit_hook(
            'httpsisoboot: [unexpected error]: %s\n' % traceback.format_exc()
        )
