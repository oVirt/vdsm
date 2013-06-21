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

CONF_TO_DOMXML = [({
    'vmId': '9ffe28b6-6134-4b1e-8804-1185f49c436f',
    'smp': '8', 'memSize': '1024',
    'displayPort': '-1', 'vmName': 'testVm',
    'display': 'vnc', 'emulatedMachine': 'pc',
    'boot': '', 'timeOffset': 0, 'tdf': True,
    'acpiEnable': 'true', 'cpuType': 'qemu64',
    'smpCoresPerSocket': 1, 'smpThreadsPerCore': 1,
    'smp': '1', 'cpuPinning': {},
    'vmchannel': 'true', 'qgaEnable': 'true',
    'tabletEnable': False,
    'displayNetwork': 'mydisp', 'custom': {}},

    """<?xml version="1.0" encoding="utf-8"?>
        <domain type="kvm">
            <name>testVm</name>
            <uuid>%(vmId)s</uuid>
            <memory>1048576</memory>
            <currentMemory>1048576</currentMemory>
            <vcpu>1</vcpu>
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
                <graphics autoport="yes" passwd="*****"
                passwdValidTo="1970-01-01T00:00:01" port="-1" type="vnc">
                <listen network="vdsm-mydisp" type="network"/>
                </graphics>
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
                </clock>
                <features>
                    <acpi/>
                </features>
                <cputune/>
                <cpu match="exact">
                    <model>qemu64</model>
                    <feature name="svm" policy="disable"/>
                    <topology cores="1" sockets="1" threads="1"/>
                </cpu>
            </domain>
""", )]
