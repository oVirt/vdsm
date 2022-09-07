# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

CONF_TO_DOMXML_X86_64 = [({
    'vmId': '9ffe28b6-6134-4b1e-8804-1185f49c436f',
    'memSize': '1024', 'memGuaranteedSize': '512',
    'displayPort': '-1', 'vmName': 'testVm',
    'display': 'vnc', 'emulatedMachine': 'pc',
    'boot': '', 'timeOffset': 0,
    'acpiEnable': 'true', 'cpuType': 'qemu64',
    'smpCoresPerSocket': 1, 'smpThreadsPerCore': 1,
    'smp': '1', 'cpuPinning': {}, 'numaTune': {}, 'maxVCpus': '160',
    'tabletEnable': False,
    'displayNetwork': 'mydisp', 'custom': {},
    'guestNumaNodes': [], 'agentChannelName': 'com.redhat.rhevm.vdsm'},

    """<?xml version="1.0" encoding="utf-8"?>
       <domain type="kvm"
               xmlns:ovirt-tune="http://ovirt.org/vm/tune/1.0"
               xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
            <name>testVm</name>
            <uuid>%(vmId)s</uuid>
            <memory>1048576</memory>
            <currentMemory>1048576</currentMemory>
            <vcpu current="1">160</vcpu>
            <devices>
                <channel type="unix">
                    <target name="%(agentChannelName)s" type="virtio"/>
                    <source mode="bind"
       path="/var/lib/libvirt/qemu/channels/%(vmId)s.%(agentChannelName)s"/>
                </channel>
                <channel type="unix">
                    <target name="org.qemu.guest_agent.0" type="virtio"/>
                    <source mode="bind"
       path="/var/lib/libvirt/qemu/channels/%(vmId)s.org.qemu.guest_agent.0"/>
                </channel>
                <input bus="ps2" type="mouse"/>
                </devices>
                <metadata>
                    <ovirt-tune:qos/>
                    <ovirt-vm:vm/>
                </metadata>
                <clock adjustment="0" offset="variable">
                    <timer name="rtc" tickpolicy="catchup"/>
                    <timer name="pit" tickpolicy="delay"/>
                    <timer name="hpet" present="no"/>
                </clock>
                <features>
                    <acpi/>
                </features>
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
    'memSize': '1024', 'memGuaranteedSize': '512',
    'displayPort': '-1', 'vmName': 'testVm',
    'display': 'vnc', 'emulatedMachine': 'pc',
    'boot': '', 'timeOffset': 0,
    'acpiEnable': 'true', 'cpuType': 'POWER8',
    'smpCoresPerSocket': 1, 'smpThreadsPerCore': 1,
    'smp': '1', 'cpuPinning': {}, 'numaTune': {}, 'maxVCpus': '160',
    'tabletEnable': False,
    'displayNetwork': 'mydisp', 'custom': {},
    'guestNumaNodes': [], 'agentChannelName': 'com.redhat.rhevm.vdsm'},

    """<?xml version="1.0" encoding="utf-8"?>
       <domain type="kvm"
               xmlns:ovirt-tune="http://ovirt.org/vm/tune/1.0"
               xmlns:ovirt-vm="http://ovirt.org/vm/1.0">
            <name>testVm</name>
            <uuid>%(vmId)s</uuid>
            <memory>1048576</memory>
            <currentMemory>1048576</currentMemory>
            <vcpu current="1">160</vcpu>
            <devices>
                <channel type="unix">
                    <target name="%(agentChannelName)s" type="virtio"/>
                    <source mode="bind"
       path="/var/lib/libvirt/qemu/channels/%(vmId)s.%(agentChannelName)s"/>
                </channel>
                <channel type="unix">
                    <target name="org.qemu.guest_agent.0" type="virtio"/>
                    <source mode="bind"
       path="/var/lib/libvirt/qemu/channels/%(vmId)s.org.qemu.guest_agent.0"/>
                </channel>
                <input bus="usb" type="mouse"/>
                <emulator>/usr/bin/qemu-system-ppc64</emulator>
                </devices>
                <metadata>
                    <ovirt-tune:qos/>
                    <ovirt-vm:vm/>
                </metadata>
                <clock adjustment="0" offset="variable">
                    <timer name="rtc" tickpolicy="catchup"/>
                    <timer name="pit" tickpolicy="delay"/>
                </clock>
                <os>
                    <type arch="ppc64" machine="pc">hvm</type>
                </os>
                <cputune/>
                <cpu>
                    <model>POWER8</model>
                    <topology cores="1" sockets="160" threads="1"/>
                    <numa/>
                </cpu>
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
        <devices>
            <channel type="unix">
                <target name="org.qemu.guest_agent.0" type="virtio"/>
                <source mode="bind"
        path="/var/lib/libvirt/qemu/channels/%(vmId)s.org.qemu.guest_agent.0"/>
            </channel>
            <channel type="unix">
                <target name="org.libguestfs.channel.0" type="virtio"
                    state="connected"/>
                <source mode="connect" path="/tmp/guestfsd.sock"/>
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


# fetched from actual VDSM logs.
# this is the output of VM.status(),
# so VDSM=>Engine.
# it is the other way around with respect to
# VM_CONF_* above.
VM_STATUS_DUMP = {
    'acpiEnable': 'true',
    'bootMenuEnable': 'false',
    'clientIp': '',
    'copyPasteEnable': 'true',
    'cpuType': 'SandyBridge',
    'custom': {
        'device_4cf4bc50-87c6-44a1-b382-339929ed8350':
        'VmDevice {vmId=56f693d4-2245-444a-a4e8-fcc5bbd08350, '
        'deviceId=4cf4bc50-87c6-44a1-b382-339929ed8350, '
        'device=ide, type=CONTROLLER, bootOrder=0, specParams={}, '
        'address={bus=0x00, domain=0x0000, type=pci, slot=0x01, '
        'function=0x1}, managed=false, plugged=true, readOnly=false, '
        'deviceAlias=ide0, customProperties={}, snapshotId=null, '
        'logicalName=null}',
        'device_4cf4bc50-87c6-44a1-b382-339929ed8350'
        'device_bb36a3fe-5333-4a66-9708-ef7dc5dca461':
        'VmDevice {vmId=56f693d4-2245-444a-a4e8-fcc5bbd08350, '
        'deviceId=bb36a3fe-5333-4a66-9708-ef7dc5dca461, '
        'device=unix, type=CHANNEL, bootOrder=0, specParams={}, '
        'address={port=1, bus=0, controller=0, type=virtio-serial}, '
        'managed=false, plugged=true, readOnly=false, '
        'deviceAlias=channel0, customProperties={}, '
        'snapshotId=null, logicalName=null}',
        'device_4cf4bc50-87c6-44a1-b382-339929ed8350'
        'device_bb36a3fe-5333-4a66-9708-ef7dc5dca461'
        'device_2c1279b8-d1ed-4b27-975a-2adf580d4eab':
        'VmDevice {vmId=56f693d4-2245-444a-a4e8-fcc5bbd08350, '
        'deviceId=2c1279b8-d1ed-4b27-975a-2adf580d4eab, '
        'device=unix, type=CHANNEL, bootOrder=0, specParams={}, '
        'address={port=2, bus=0, controller=0, '
        'type=virtio-serial}, managed=false, plugged=true, '
        'readOnly=false, deviceAlias=channel1, '
        'customProperties={}, snapshotId=null, '
        'logicalName=null}',
        'device_4cf4bc50-87c6-44a1-b382-339929ed8350'
        'device_bb36a3fe-5333-4a66-9708-ef7dc5dca461'
        'device_2c1279b8-d1ed-4b27-975a-2adf580d4eab'
        'device_d8494628-79cf-4d44-84bb-609acd6d0510':
        'VmDevice {vmId=56f693d4-2245-444a-a4e8-fcc5bbd08350, '
        'deviceId=d8494628-79cf-4d44-84bb-609acd6d0510, '
        'device=spicevmc, type=CHANNEL, bootOrder=0, specParams={}, '
        'address={port=3, bus=0, controller=0, type=virtio-serial}, '
        'managed=false, plugged=true, readOnly=false, '
        'deviceAlias=channel2, customProperties={}, snapshotId=null, '
        'logicalName=null}'},
    'devices': [
        {'address': {'bus': '0x00',
                     'domain': '0x0000',
                     'function': '0x0',
                     'slot': '0x02',
                     'type': 'pci'},
         'device': 'qxl',
         'deviceId': '400d7f28-9c94-487f-b8a4-8fccb84a9910',
         'deviceType': 'video',
         'specParams': {'heads': '1', 'vram': '32768'},
         'type': 'video'},
        {'address': {'bus': '1',
                     'controller': '0',
                     'target': '0',
                     'type': 'drive',
                     'unit': '0'},
         'device': 'cdrom',
         'deviceId': '93c65974-b73c-43bc-865a-7b0775a93040',
         'deviceType': 'disk',
         'iface': 'ide',
         'index': '2',
         'path': '',
         'readonly': 'true',
         'shared': 'false',
         'specParams': {'path': ''},
         'type': 'disk'},
        {'address': {'bus': '0x00',
                     'domain': '0x0000',
                     'function': '0x0',
                     'slot': '0x04',
                     'type': 'pci'},
         'bootOrder': '1',
         'device': 'disk',
         'deviceId': '4d1ab324-2e47-4293-9b6f-189f1c730526',
         'deviceType': 'disk',
         'domainID': 'c35580cb-4f22-4272-b236-8d8155ac3111',
         'format': 'cow',
         'iface': 'virtio',
         'imageID': '4d1ab324-2e47-4293-9b6f-189f1c730526',
         'index': 0,
         'optional': 'false',
         'poolID': '00000002-0002-0002-0002-00000000014b',
         'propagateErrors': 'off',
         'readonly': 'false',
         'shared': 'false',
         'specParams': {},
         'type': 'disk',
         'volumeID': 'ea78413a-106e-464d-972f-bf5d002220ab'},
        {'address': {'bus': '0x00',
                     'domain': '0x0000',
                     'function': '0x0',
                     'slot': '0x06',
                     'type': 'pci'},
         'device': 'memballoon',
         'deviceId': '7d6116b1-0ddb-409e-9721-3395ba5e3c2f',
         'deviceType': 'balloon',
         'specParams': {'model': 'virtio'},
         'type': 'balloon'},
        {'address': {'bus': '0x00',
                     'domain': '0x0000',
                     'function': '0x0',
                     'slot': '0x05',
                     'type': 'pci'},
         'device': 'scsi',
         'deviceId': '288058bc-02d9-4fb7-b0dd-89086eda6e0d',
         'deviceType': 'controller',
         'index': '0',
         'model': 'virtio-scsi',
         'specParams': {},
         'type': 'controller'},
        {'address': {'bus': '0x00',
                     'domain': '0x0000',
                     'function': '0x0',
                     'slot': '0x03',
                     'type': 'pci'},
         'device': 'virtio-serial',
         'deviceId': 'ae504a74-3d1d-4a74-81fe-b02f08955e88',
         'deviceType': 'controller',
         'specParams': {},
         'type': 'controller'}],
    'display': 'qxl',
    'displayIp': '192.168.1.48',
    'displayNetwork': 'ovirtmgmt',
    'displayPort': '-1',
    'displaySecurePort': '-1',
    'emulatedMachine': 'rhel6.5.0',
    'fileTransferEnable': 'true',
    'guestDiskMapping': {},
    'keyboardLayout': 'en-us',
    'kvmEnable': 'true',
    'launchPaused': 'true',
    'maxVCpus': '16',
    'memGuaranteedSize': 16,
    'memSize': 16,
    'nice': '0',
    'pid': '0',
    'smartcardEnable': 'false',
    'smp': '1',
    'smpCoresPerSocket': '1',
    'spiceSecureChannels': 'smain,sinputs,scursor,splayback,'
            'srecord,sdisplay,susbredir,ssmartcard',
    'spiceSslCipherSuite': 'DEFAULT',
    'status': 'WaitForLaunch',
    'timeOffset': '0',
    'transparentHugePages': 'true',
    'vmId': '56f693d4-2245-444a-a4e8-fcc5bbd08350',
    'vmName': 'NS_C021',
    'vmType': 'kvm'}
