#
# Copyright Red Hat 2013
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

CONF_TO_DOMXML_X86_64 = [({
    'vmId': '9ffe28b6-6134-4b1e-8804-1185f49c436f',
    'smp': '8', 'memSize': '1024', 'memGuaranteedSize': '512',
    'displayPort': '-1', 'vmName': 'testVm',
    'display': 'vnc', 'emulatedMachine': 'pc',
    'boot': '', 'timeOffset': 0,
    'acpiEnable': 'true', 'cpuType': 'qemu64',
    'smpCoresPerSocket': 1, 'smpThreadsPerCore': 1,
    'smp': '1', 'cpuPinning': {}, 'numaTune': {}, 'maxVCpus': '160',
    'tabletEnable': False,
    'displayNetwork': 'mydisp', 'custom': {},
    'guestNumaNodes': []},

    """<?xml version="1.0" encoding="utf-8"?>
        <domain type="kvm">
            <name>testVm</name>
            <uuid>%(vmId)s</uuid>
            <memory>1048576</memory>
            <currentMemory>1048576</currentMemory>
            <vcpu current="1">160</vcpu>
            <memtune>
                <min_guarantee>524288</min_guarantee>
            </memtune>
            <devices>
                <channel type="unix">
                    <target name="com.redhat.rhevm.vdsm" type="virtio"/>
                    <source mode="bind"
       path="/var/lib/libvirt/qemu/channels/%(vmId)s.com.redhat.rhevm.vdsm"/>
                </channel>
                <channel type="unix">
                    <target name="org.qemu.guest_agent.0" type="virtio"/>
                    <source mode="bind"
       path="/var/lib/libvirt/qemu/channels/%(vmId)s.org.qemu.guest_agent.0"/>
                </channel>
                <input bus="ps2" type="mouse"/>
                </devices>
                <os>
                    <type arch="x86_64" machine="pc">hvm</type>
                    <smbios mode="sysinfo"/>
                </os>
                <sysinfo type="smbios">
                    <system>
                        <entry name="manufacturer">oVirt</entry>
                        <entry name="product">oVirt Node</entry>
                        <entry name="version">18-1</entry>
      <entry name="serial">fc25cbbe-5520-4f83-b82e-1541914753d9</entry>
                        <entry name="uuid">%(vmId)s</entry>
                    </system>
                </sysinfo>
                <clock adjustment="0" offset="variable">
                    <timer name="rtc" tickpolicy="catchup"/>
                    <timer name="pit" tickpolicy="delay"/>
                    <timer name="hpet" present="no"/>
                </clock>
                <features>
                    <acpi/>
                </features>
                <cputune/>
                <cpu match="exact">
                    <model>qemu64</model>
                    <feature name="svm" policy="disable"/>
                    <topology cores="1" sockets="160" threads="1"/>
                    <numa/>
                </cpu>
            </domain>
""", )]

CONF_TO_DOMXML_PPC64 = [({
    'vmId': '9ffe28b6-6134-4b1e-8804-1185f49c436f',
    'smp': '8', 'memSize': '1024', 'memGuaranteedSize': '512',
    'displayPort': '-1', 'vmName': 'testVm',
    'display': 'vnc', 'emulatedMachine': 'pc',
    'boot': '', 'timeOffset': 0,
    'acpiEnable': 'true', 'cpuType': 'qemu64',
    'smpCoresPerSocket': 1, 'smpThreadsPerCore': 1,
    'smp': '1', 'cpuPinning': {}, 'numaTune': {}, 'maxVCpus': '160',
    'tabletEnable': False,
    'displayNetwork': 'mydisp', 'custom': {},
    'guestNumaNodes': []},

    """<?xml version="1.0" encoding="utf-8"?>
        <domain type="kvm"
        xmlns:qemu="http://libvirt.org/schemas/domain/qemu/1.0">
            <name>testVm</name>
            <uuid>%(vmId)s</uuid>
            <memory>1048576</memory>
            <currentMemory>1048576</currentMemory>
            <vcpu current="1">160</vcpu>
            <memtune>
                <min_guarantee>524288</min_guarantee>
            </memtune>
            <devices>
                <channel type="unix">
                    <target name="com.redhat.rhevm.vdsm" type="virtio"/>
                    <source mode="bind"
       path="/var/lib/libvirt/qemu/channels/%(vmId)s.com.redhat.rhevm.vdsm"/>
                </channel>
                <channel type="unix">
                    <target name="org.qemu.guest_agent.0" type="virtio"/>
                    <source mode="bind"
       path="/var/lib/libvirt/qemu/channels/%(vmId)s.org.qemu.guest_agent.0"/>
                </channel>
                <input bus="usb" type="mouse"/>
                <emulator>/usr/bin/qemu-system-ppc64</emulator>
                </devices>
                <os>
                    <type arch="ppc64" machine="pc">hvm</type>
                </os>
                <clock adjustment="0" offset="variable">
                    <timer name="rtc" tickpolicy="catchup"/>
                    <timer name="pit" tickpolicy="delay"/>
                </clock>
                <cputune/>
                <cpu>
                    <topology cores="1" sockets="160" threads="1"/>
                    <numa/>
                </cpu>
                <qemu:commandline>
                    <qemu:arg value="-usbdevice"/>
                    <qemu:arg value="keyboard"/>
                </qemu:commandline>
            </domain>
""", )]


# valid libvirt domain XML, but not generated by VDSM
CONF_TO_DOMXML_NO_VDSM = [({
    'vmId': ''},

    """<domain type="kvm">
        <name>SuperTiny_C0</name>
        <uuid>%(vmId)s</uuid>
        <memory>65536</memory>
        <currentMemory>65536</currentMemory>
        <vcpu current="1">160</vcpu>
        <memtune>
            <min_guarantee>16384</min_guarantee>
        </memtune>
        <devices>
            <channel type="unix">
                <target name="org.qemu.guest_agent.0" type="virtio"/>
                <source mode="bind"
        path="/var/lib/libvirt/qemu/channels/%(vmId)s.org.qemu.guest_agent.0"/>
            </channel>
            <input bus="ps2" type="mouse"/>
            <memballoon model="none"/>
            <controller index="0" model="virtio-scsi" type="scsi">
                <address bus="0x00" domain="0x0000" function="0x0" slot="0x03"
                    type="pci"/>
            </controller>
            <video>
                <address bus="0x00" domain="0x0000" function="0x0" slot="0x02"
                    type="pci"/>
                <model heads="1" type="qxl" vram="32768"/>
            </video>
            <graphics autoport="yes" keymap="en-us" passwd="*****"
        passwdValidTo="1970-01-01T00:00:01"
        port="-1" tlsPort="-1" type="spice">
                <listen network="vdsm-ovirtmgmt" type="network"/>
            </graphics>
            <disk device="cdrom" snapshot="no" type="file">
                <address bus="1" controller="0" target="0"
        type="drive" unit="0"/>
                <source file="" startupPolicy="optional"/>
                <target bus="ide" dev="hdc"/>
                <readonly/>
                <serial/>
                <boot order="1"/>
            </disk>
            <channel type="spicevmc">
                <target name="com.redhat.spice.0" type="virtio"/>
            </channel>
        </devices>
        <os>
            <type arch="x86_64" machine="rhel6.5.0">hvm</type>
            <smbios mode="sysinfo"/>
        </os>
        <sysinfo type="smbios">
            <system>
                <entry name="manufacturer">oVirt</entry>
                <entry name="product">oVirt Node</entry>
                <entry name="version">6Server-6.5.0.1.el6</entry>
                <entry name="uuid">%(vmId)s</entry>
            </system>
        </sysinfo>
        <clock adjustment="0" offset="variable">
            <timer name="rtc" tickpolicy="catchup"/>
            <timer name="pit" tickpolicy="delay"/>
            <timer name="hpet" present="no"/>
        </clock>
        <features>
            <acpi/>
        </features>
        <cpu match="exact">
            <model>SandyBridge</model>
            <topology cores="1" sockets="160" threads="1"/>
        </cpu>
    </domain>
""", )]
