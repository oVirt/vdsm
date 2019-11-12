#!/usr/bin/python3
#
# Copyright 2011 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

from __future__ import absolute_import
from __future__ import print_function

'''
To Enable this set fake_kvm_support=true in /etc/vdsm/vdsm.conf.
'''
from functools import wraps
import getopt
import sys
from xml.dom import minidom, NotFoundErr

from vdsm.common import cpuarch
from vdsm.config import config


_WORKAROUNDS = []
_WORKAROUND_TESTS = []


def _usage():
    print('Usage: {} option'.format(sys.argv[0]))
    print('\t-h, --help\t\tdisplay this help')
    print('\t-t, --test\t\trun tests')


def workaround(real_arch=None, effective_arch=None):
    '''
    Method to register workaround. The workaround is applied when VDSM runs
    on the source architecture (real_arch) but tries to act as
    desired architecture (effective_arch). Leaving either argument as None
    means that it will be applied always on real or effective architecture
    respectively.

    Params:

    real_arch        The real architecture required for workaround to be
                     applied. For example x86_64 on real x86_64 host or
                     ppc64le on real ppc64le host.

    effective_arch   The target architecture for which workaround will be
                     applied. For example when running x86_64 machine as
                     ppc64le the arch is x86_64 and target_arch is ppc64le.
    '''
    def workaround(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            return function(*args, **kwargs)

        if ((not real_arch or real_arch == cpuarch.real()) and
                (not effective_arch or effective_arch == cpuarch.effective())):
            _WORKAROUNDS.append(wrapped)

        return wrapped
    return workaround


def workaround_testcase():
    '''
    Method to register workaround test
    '''
    def workaround(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            return function(*args, **kwargs)
        _WORKAROUND_TESTS.append(wrapped)
        return wrapped
    return workaround


@workaround()
def _set_qemu(domxml):
    domxml.documentElement.setAttribute('type', 'qemu')


@workaround()
def _os_set_arch(domxml):
    ostag = domxml.getElementsByTagName('os')[0]
    typetag = ostag.getElementsByTagName('type')[0]
    typetag.setAttribute('arch', cpuarch.real())


@workaround(real_arch=cpuarch.PPC64LE)
def _graphics_spice_to_vnc(domxml):
    graphics = domxml.getElementsByTagName('graphics')[0]
    graphics.setAttribute('type', 'vnc')


@workaround(real_arch=cpuarch.PPC64LE,
            effective_arch=cpuarch.X86_64)
def _video_spice_to_vnc(domxml):
    videotag = domxml.getElementsByTagName('video')[0]
    modeltag = videotag.getElementsByTagName('model')[0]
    modeltag.setAttribute('type', 'vga')

    try:
        modeltag.removeAttribute('ram')
    except NotFoundErr:
        pass

    try:
        modeltag.removeAttribute('vgamem')
    except NotFoundErr:
        pass


@workaround()
def _cpu_remove_model(domxml):
    cputag = domxml.getElementsByTagName('cpu')[0]
    modeltag = cputag.getElementsByTagName('model')[0]
    cputag.removeChild(modeltag)


@workaround(real_arch=cpuarch.PPC64LE,
            effective_arch=cpuarch.X86_64)
def _smartcard_remove(domxml):
    smartcardtag = domxml.getElementsByTagName('smartcard')
    for tag in smartcardtag:
        tag.parentNode.removeChild(tag)


@workaround(real_arch=cpuarch.X86_64,
            effective_arch=cpuarch.PPC64LE)
def _controller_remove_spapr(domxml):
    for controllertag in domxml.getElementsByTagName('controller'):
        for child in controllertag.childNodes:
            try:
                if child.getAttribute('type') == 'spapr-vio':
                    child.parentNode.removeChild(child)
            except AttributeError:
                continue


@workaround(real_arch=cpuarch.X86_64,
            effective_arch=cpuarch.PPC64LE)
def _os_set_machine_type_i440fx(domxml):
        ostag = domxml.getElementsByTagName('os')[0]
        typetag = ostag.getElementsByTagName('type')[0]
        typetag.setAttribute('machine', 'pc-i440fx-rhel7.1.0')


@workaround(real_arch=cpuarch.PPC64LE,
            effective_arch=cpuarch.X86_64)
def _os_set_machine_type_pseries(domxml):
        ostag = domxml.getElementsByTagName('os')[0]
        typetag = ostag.getElementsByTagName('type')[0]
        typetag.setAttribute('machine', 'pseries')
        bios = ostag.getElementsByTagName('bios')[0]
        bios.parentNode.removeChild(bios)


@workaround(real_arch=cpuarch.PPC64LE)
def _sound_remove(domxml):
        soundtag = domxml.getElementsByTagName('sound')
        # may not be present
        for tag in soundtag:
            tag.parentNode.removeChild(tag)


@workaround(real_arch=cpuarch.PPC64LE,
            effective_arch=cpuarch.X86_64)
def _sysinfo_remove_smbios(domxml):
        sysinfotag = domxml.getElementsByTagName('sysinfo')[0]
        sysinfotag.parentNode.removeChild(sysinfotag)

        smbiostag = domxml.getElementsByTagName('smbios')[0]
        smbiostag.parentNode.removeChild(smbiostag)


@workaround(real_arch=cpuarch.PPC64LE,
            effective_arch=cpuarch.X86_64)
def _disk_remove_ide(domxml):
    for disktag in domxml.getElementsByTagName('disk'):
        if disktag.getElementsByTagName(
                'target')[0].getAttribute('bus') == 'ide':
            disktag.parentNode.removeChild(disktag)


@workaround(real_arch=cpuarch.PPC64LE,
            effective_arch=cpuarch.X86_64)
def _channel_remove_spicevmc(domxml):
    for channeltag in domxml.getElementsByTagName('channel'):
        if channeltag.getAttribute('type') == 'spicevmc':
            channeltag.parentNode.removeChild(channeltag)


@workaround(real_arch=cpuarch.PPC64LE,
            effective_arch=cpuarch.X86_64)
def _memory_lower_max(domxml):
    maxmemorytag = domxml.getElementsByTagName('maxMemory')[0]
    maxmemorytag.setAttribute('slots', '2')
    maxmemorytag.firstChild.nodeValue = '1073741824'


@workaround()
def _memory_update(domxml):
    value = config.get('vars', 'fake_kvm_memory')
    if value != '0':
        for memtag in ('memory', 'currentMemory'):
            memvalue = domxml.getElementsByTagName(memtag)[0]
            while memvalue.firstChild:
                memvalue.removeChild(memvalue.firstChild)

            memvalue.appendChild(domxml.createTextNode(value))


@workaround_testcase()
def _sound_remove_test():
    domxml = minidom.parseString('''
    <devices>
        <sound model="ich6"/>
    </devices>
    ''')

    _sound_remove(domxml)

    return (not domxml.getElementsByTagName('sound'))


@workaround_testcase()
def _set_qemu_test():
    domxml = minidom.parseString('''
    <domain type="kvm"
        xmlns:qemu="http://libvirt.org/schemas/domain/qemu/1.0"/>
    ''')

    _set_qemu(domxml)

    return domxml.documentElement.getAttribute('type') == 'qemu'


@workaround_testcase()
def _os_set_arch_test():
    domxml = minidom.parseString('''
    <os>
        <type arch="x86_64" machine="pc-i440fx-rhel7.2.0">hvm</type>
        <smbios mode="sysinfo"/>
    </os>
    ''')

    _os_set_arch(domxml)

    return (domxml.getElementsByTagName(
        'type')[0].getAttribute('arch') == cpuarch.real())


@workaround_testcase()
def _graphics_spice_to_vnc_test():
    domxml = minidom.parseString('''
    <graphics autoport="yes" passwd="*****" passwdValidTo="1970-01-01T00:00:01"
                                 port="-1" tlsPort="-1" type="spice">
        <listen network="vdsm-ovirtmgmt" type="network"/>
    </graphics>
    ''')

    _graphics_spice_to_vnc(domxml)

    return (domxml.getElementsByTagName(
        'graphics')[0].getAttribute('type') == 'vnc')


@workaround_testcase()
def _video_spice_to_vnc_test():
    domxml = minidom.parseString('''
    <video>
            <address bus="0x00" domain="0x0000" function="0x0" slot="0x02"
                                type="pci"/>
            <model heads="1" ram="1234" vgamem="4321" type="qxl" vram="32768"/>
    </video>
    ''')

    _video_spice_to_vnc(domxml)

    return (domxml.getElementsByTagName('video')[0].getElementsByTagName(
        'model')[0].getAttribute('type') == 'vga' and
        not domxml.getElementsByTagName('video')[0].getElementsByTagName(
            'model')[0].getAttribute('vgamem') and
        not domxml.getElementsByTagName('video')[0].getElementsByTagName(
            'model')[0].getAttribute('ram'))


@workaround_testcase()
def _cpu_remove_model_test():
    domxml = minidom.parseString('''
    <cpu match="exact">
            <model>Conroe</model>
            <topology cores="1" sockets="16" threads="1"/>
            <numa>
                    <cell cpus="0" memory="1048576"/>
            </numa>
    </cpu>
    ''')

    _cpu_remove_model(domxml)

    return (not domxml.getElementsByTagName('model'))


@workaround_testcase()
def _smartcard_remove_test():
    domxml = minidom.parseString('''
    <devices>
        <smartcard mode="passthrough" type="spicevmc"/>
    </devices>
    ''')

    _smartcard_remove(domxml)

    return (not domxml.getElementsByTagName('smartcard'))


@workaround_testcase()
def _controller_remove_spapr_test():
    domxml = minidom.parseString('''
    <devices>
        <controller index="0" type="scsi">
                <address type="spapr-vio"/>
        </controller>
    </devices>
    ''')

    _controller_remove_spapr(domxml)

    return not domxml.getElementsByTagName('address')


@workaround_testcase()
def _os_set_machine_type_pseries_test():
    domxml = minidom.parseString('''
    <os>
        <type arch="x86_64" machine="pc-i440fx-rhel7.2.0">hvm</type>
        <smbios mode="sysinfo"/>
        <bios useserial="yes"/>
    </os>
    ''')

    _os_set_machine_type_pseries(domxml)

    return (domxml.getElementsByTagName(
        'type')[0].getAttribute('machine') == 'pseries' and
        not domxml.getElementsByTagName('bios'))


@workaround_testcase()
def _os_set_machine_type_i440fx_test():
    domxml = minidom.parseString('''
    <os>
        <type arch="x86_64" machine="pseries">hvm</type>
        <smbios mode="sysinfo"/>
    </os>
    ''')

    _os_set_machine_type_i440fx(domxml)

    return (domxml.getElementsByTagName('type')[0].getAttribute('machine') ==
            'pc-i440fx-rhel7.1.0')


@workaround_testcase()
def _sysinfo_remove_smbios_test():
    domxml = minidom.parseString('''
    <domain>
        <sysinfo type="smbios">
                <system>
                        <entry name="manufacturer">oVirt</entry>
                </system>
        </sysinfo>
        <os>
            <type arch="x86_64" machine="pc-i440fx-rhel7.2.0">hvm</type>
            <smbios mode="sysinfo"/>
        </os>
    </domain>
    ''')

    _sysinfo_remove_smbios(domxml)

    return (not domxml.getElementsByTagName('sysinfo') and
            not domxml.getElementsByTagName('smbios'))


@workaround_testcase()
def _disk_remove_ide_test():
    domxml = minidom.parseString('''
    <devices>
        <disk device="cdrom" snapshot="no" type="file">
            <address bus="1" controller="0" target="0" type="drive" unit="0"/>
            <source file="" startupPolicy="optional"/>
            <target bus="ide" dev="hdc"/>
            <readonly/>
            <serial/>
        </disk>
        <disk device="disk" snapshot="no" type="block">
            <source dev="/not/relevant/anyway"/>
            <target bus="virtio" dev="vda"/>
            <serial>not-relevant-anyway</serial>
            <driver cache="none" error_policy="stop" io="native"
                                 name="qemu" type="raw"/>
        </disk>
    </devices>
    ''')

    _disk_remove_ide(domxml)

    return len(domxml.getElementsByTagName('disk')) == 1


@workaround_testcase()
def _channel_remove_spicevmc_test():
    domxml = minidom.parseString('''
    <devices>
        <channel type="spicevmc">
            <target name="com.redhat.spice.0" type="virtio"/>
        </channel>
    </devices>
    ''')

    _channel_remove_spicevmc(domxml)

    return not domxml.getElementsByTagName('channel')


@workaround_testcase()
def _memory_lower_max_test():
    domxml = minidom.parseString('''
    <domain type="kvm"
            xmlns:ovirt-tune="http://ovirt.org/vm/tune/1.0"
            xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
        <currentMemory>1048576</currentMemory>
        <maxMemory slots="16">4294967296</maxMemory>
    </domain>
    ''')

    _memory_lower_max(domxml)

    return (domxml.getElementsByTagName(
        'maxMemory')[0].getAttribute('slots') == '2' and
        domxml.getElementsByTagName(
            'maxMemory')[0].childNodes[0].nodeValue == '1073741824')


def _test():
    for test in _WORKAROUND_TESTS:
        print('{:<70}{:>10}'.format(test.__name__, 'ok' if test() else 'fail'))


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

    fake_kvm_support = config.getboolean('vars', 'fake_kvm_support')

    if fake_kvm_support:
        # Why here? So anyone can run -t and -h without setting the path.
        try:
            import hooking
        except ImportError:
            print('Could not import hooking module. You should only run this '
                  'script directly with option specified.')
            _usage()
            sys.exit(1)

        domxml = hooking.read_domxml()
        for workaround in _WORKAROUNDS:
            workaround(domxml)
        hooking.write_domxml(domxml)
