# Copyright 2019-2022 Red Hat, Inc.
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

import io

from vdsm.virt.libvirthook import vm_libvirt_hook

from testlib import normalized


_ORIG_XML = '''<domain xmlns:ns0="http://ovirt.org/vm/tune/1.0"
                       xmlns:ovirt-vm="http://ovirt.org/vm/1.0" type="kvm">
    <name>test</name>
    <devices>
        <channel type="spicevmc">
            <target name="com.redhat.spice.0" type="virtio" />
        </channel>
        <disk device="cdrom" snapshot="no" type="file">
            <driver error_policy="report" name="qemu" type="raw" />
            <source file="" startupPolicy="optional"/>
            <target bus="ide" dev="hdc" />
            <readonly />
        </disk>
        <disk device="disk" snapshot="no" type="file">
            <target bus="scsi" dev="sda" />
            <source file="/path/to/file"/>
        </disk>
        <disk device="disk" snapshot="no" type="file">
            <target bus="scsi" dev="sdb" />
            <source file="/path/to/file">
                <something>inside</something>
            </source>
        </disk>
        <disk device="disk" snapshot="no" type="file">
            <target bus="scsi" dev="sdc" />
            <source file="/path/to/file">
                <seclabel model="dac" relabel="yes" />
            </source>
        </disk>
        <disk device="disk" snapshot="no" type="network">
            <source name="poolname/volumename" protocol="rbd">
                <host name="1.2.3.4" port="6789" transport="tcp"/>
            </source>
        </disk>
        <disk device="disk" type="unknown">
            <source name="poolname/volumename" protocol="rbd"/>
        </disk>
        <disk device="disk" snapshot="no" type="dev">
            <source file="/path/to/dev">
                <seclabel model="dac" relabel="no" type="none" />
            </source>
        </disk>
        <interface type="bridge">
            <model type="virtio" />
            <link state="up" />
            <source bridge="ovirtmgmt" />
        </interface>
        <graphics autoport="yes" passwd="12characters"
                  passwdValidTo="1970-01-01T00:00:01"
                  port="-1" tlsPort="-1" type="vnc">
            <channel mode="secure" name="main"/>
            <channel mode="secure" name="inputs"/>
            <channel mode="secure" name="cursor"/>
            <channel mode="secure" name="playback"/>
            <channel mode="secure" name="record"/>
            <channel mode="secure" name="display"/>
            <channel mode="secure" name="smartcard"/>
            <channel mode="secure" name="usbredir"/>
            <listen network="vdsm-ovirtmgmt" type="network"/>
        </graphics>
    </devices>
    <metadata>
        <ns0:qos />
        <ovirt-vm:vm>
            <ovirt-vm:device devtype="disk" name="sda">
                <ovirt-vm:poolID>120a3ee2-5e8e-11e8-af64-525400dfa5a4</ovirt-vm:poolID>
                <ovirt-vm:volumeID>439cffc7-551c-4975-a404-72d7e216a115</ovirt-vm:volumeID>
                <ovirt-vm:imageID>2710a43b-7925-42b5-9f29-afc54e4d6f9c</ovirt-vm:imageID>
                <ovirt-vm:domainID>b32ebbee-ee25-42c8-90ea-77bc76ecbcb4</ovirt-vm:domainID>
            </ovirt-vm:device>
        </ovirt-vm:vm>
    </metadata>
</domain>
'''

_MODIFIED_XML = '''<domain xmlns:ns0="http://ovirt.org/vm/tune/1.0"
                           xmlns:ovirt-vm="http://ovirt.org/vm/1.0" type="kvm">
    <name>test</name>
    <devices>
        <channel type="spicevmc">
            <target name="com.redhat.spice.0" type="virtio" />
        </channel>
        <disk device="cdrom" snapshot="no" type="file">
            <driver error_policy="report" name="qemu" type="raw" />
            <source file="" startupPolicy="optional">
                <seclabel model="dac" relabel="no" type="none" />
            </source>
            <target bus="ide" dev="hdc" />
            <readonly />
        </disk>
        <disk device="disk" snapshot="no" type="file">
            <target bus="scsi" dev="sda" />
            <source file="/path/to/file">
                <seclabel model="dac" relabel="no" type="none" />
            </source>
        </disk>
        <disk device="disk" snapshot="no" type="file">
            <target bus="scsi" dev="sdb" />
            <source file="/path/to/file">
                <something>inside</something>
                <seclabel model="dac" relabel="no" type="none" />
            </source>
        </disk>
        <disk device="disk" snapshot="no" type="file">
            <target bus="scsi" dev="sdc" />
            <source file="/path/to/file">
                <seclabel model="dac" relabel="yes" />
            </source>
        </disk>
        <disk device="disk" snapshot="no" type="network">
            <source name="poolname/volumename" protocol="rbd">
                <host name="1.2.3.4" port="6789" transport="tcp"/>
            </source>
        </disk>
        <disk device="disk" type="unknown">
            <source name="poolname/volumename" protocol="rbd"/>
        </disk>
        <disk device="disk" snapshot="no" type="dev">
            <source file="/path/to/dev">
                <seclabel model="dac" relabel="no" type="none" />
            </source>
        </disk>
        <interface type="bridge">
            <model type="virtio" />
            <link state="up" />
            <source bridge="ovirtmgmt" />
        </interface>
        <graphics autoport="yes" passwd="12charac"
                  passwdValidTo="1970-01-01T00:00:01"
                  port="-1" tlsPort="-1" type="vnc">
            <channel mode="secure" name="main"/>
            <channel mode="secure" name="inputs"/>
            <channel mode="secure" name="cursor"/>
            <channel mode="secure" name="playback"/>
            <channel mode="secure" name="record"/>
            <channel mode="secure" name="display"/>
            <channel mode="secure" name="smartcard"/>
            <channel mode="secure" name="usbredir"/>
            <listen network="vdsm-ovirtmgmt" type="network"/>
        </graphics>
    </devices>
    <metadata>
        <ns0:qos />
        <ovirt-vm:vm>
            <ovirt-vm:device devtype="disk" name="sda">
                <ovirt-vm:poolID>120a3ee2-5e8e-11e8-af64-525400dfa5a4</ovirt-vm:poolID>
                <ovirt-vm:volumeID>439cffc7-551c-4975-a404-72d7e216a115</ovirt-vm:volumeID>
                <ovirt-vm:imageID>2710a43b-7925-42b5-9f29-afc54e4d6f9c</ovirt-vm:imageID>
                <ovirt-vm:domainID>b32ebbee-ee25-42c8-90ea-77bc76ecbcb4</ovirt-vm:domainID>
            </ovirt-vm:device>
        </ovirt-vm:vm>
    </metadata>
</domain>
'''


class TestMigrateHook:

    def _test_hook(self, xml, modified_xml,
                   domain='foo', event='migrate', phase='begin'):
        stdin = io.StringIO(xml)
        stdout = io.StringIO()
        vm_libvirt_hook.main(domain, event, phase, stdin=stdin, stdout=stdout)
        assert normalized(stdout.getvalue()) == normalized(modified_xml)

    def test_empty(self):
        xml = '<domain/>'
        self._test_hook(xml, xml)

    def test_modifications(self):
        self._test_hook(_ORIG_XML, _MODIFIED_XML)
