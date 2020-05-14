# Copyright 2020 Red Hat, Inc.
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

import pytest

import hooking

from vhostmd import before_vm_start as vhostmd_before


DOM_XML = """
    <domain type='kvm' id='4'>
      <name>vm0</name>
      <uuid>bf9a39f7-a7f6-4727-a78e-7cc9e54c8492</uuid>
      <memory unit='KiB'>655360</memory>
      <currentMemory unit='KiB'>655360</currentMemory>
      <vcpu placement='static' current='1'>16</vcpu>
      <iothreads>1</iothreads>
      <devices>
        <emulator>/usr/libexec/qemu-kvm</emulator>
        <disk type='file' device='disk' snapshot='no'>
          <driver name='qemu' type='qcow2' cache='none' error_policy='stop' io='threads' iothread='1'/>
          <source file='/abc'>
            <seclabel model='dac' relabel='no'/>
          </source>
          <target dev='vda' bus='virtio'/>
          <serial>739b0002-0b2c-45d4-9db3-f98c3724e0d4</serial>
          <boot order='1'/>
          <alias name='ua-739b0002-0b2c-45d4-9db3-f98c3724e0d4'/>
          <address type='pci' domain='0x0000' bus='0x06' slot='0x00' function='0x0'/>
        </disk>
      </devices>
    </domain>
"""  # NOQA: E501 (potentially long line)


VHOSTMD_CONF = """
    <vhostmd>
      <globals>
      <disk>
        <name>host-metrics-disk</name>
        <path>{}</path>
        <size unit="k">256</size>
      </disk>
      <virtio>
        <max_channels>1024</max_channels>
        <expiration_time>15</expiration_time>
      </virtio>
      <transport>vbd</transport>
      <transport>virtio</transport>
      </globals>
    </vhostmd>
""".format(vhostmd_before.DEFAULT_VBD_PATH)


@pytest.fixture(autouse=True)
def fake_subprocess_call(monkeypatch):
    monkeypatch.setattr(vhostmd_before.subprocess, "call", lambda args: None)


@pytest.fixture
def vhostmd_conf(request, tmpdir):
    conf_contents = getattr(request, "param", VHOSTMD_CONF)
    conf_file = tmpdir.join("vhostmd.conf")
    conf_file.write(conf_contents)
    return vhostmd_before.VhostmdConf(str(conf_file))


# https://github.com/vhostmd/vhostmd/blob/52b2dbf5c7136f87b1e6a6f4a10c363779bd1fb4/README#L59
@pytest.mark.parametrize(
    "vhostmd_conf, expected_transports",
    indirect=["vhostmd_conf"],
    argvalues=[
        pytest.param(
            """
            <vhostmd>
              <globals>
                <transport>vbd</transport>
              </globals>
            </vhostmd>
            """,
            {vhostmd_before.VhostmdTransport.VBD},
            id="vbd only"
        ),
        pytest.param(
            """
            <vhostmd>
              <globals>
                <transport>virtio</transport>
              </globals>
            </vhostmd>
            """,
            {vhostmd_before.VhostmdTransport.VIRTIO},
            id="virtio only"
        ),
        pytest.param(
            """
            <vhostmd>
              <globals>
                <transport>vbd</transport>
                <transport>virtio</transport>
              </globals>
            </vhostmd>
            """,
            {
                vhostmd_before.VhostmdTransport.VBD,
                vhostmd_before.VhostmdTransport.VIRTIO
            },
            id="vbd and virtio"
        ),
    ]
)
def test_vhostmdconf_should_detect_used_transports(vhostmd_conf,
                                                   expected_transports):
    assert vhostmd_conf.transports == expected_transports


# https://github.com/vhostmd/vhostmd/blob/52b2dbf5c7136f87b1e6a6f4a10c363779bd1fb4/README#L48
# https://github.com/vhostmd/vhostmd/blob/52b2dbf5c7136f87b1e6a6f4a10c363779bd1fb4/vhostmd/vhostmd.c#L95
@pytest.mark.parametrize(
    "vhostmd_conf, expected_vbd_path",
    indirect=["vhostmd_conf"],
    argvalues=[
        pytest.param(
            """
            <vhostmd>
              <globals>
                <disk>
                  <name>host-metrics-disk</name>
                  <path>/dev/my/vhostmd</path>
                  <size unit="k">256</size>
                </disk>
                <transport>vbd</transport>
              </globals>
            </vhostmd>
            """,
            "/dev/my/vhostmd",
            id="custom vbd path"
        ),
        pytest.param(
            """
            <vhostmd>
              <globals>
                <disk>
                  <name>host-metrics-disk</name>
                  <size unit="k">256</size>
                </disk>
                <transport>vbd</transport>
              </globals>
            </vhostmd>
            """,
            vhostmd_before.DEFAULT_VBD_PATH,
            id="default vbd path"
        ),
    ]
)
def test_vhostmdconf_should_provide_path_to_vbd(vhostmd_conf,
                                                expected_vbd_path):
    assert vhostmd_conf.vbd_path == expected_vbd_path


@pytest.fixture
def dom_xml(monkeypatch, tmpdir):
    dom_xml_file = tmpdir.join("dom.xml")
    dom_xml_file.write(DOM_XML)
    monkeypatch.setenv("_hook_domxml", str(dom_xml_file))


@pytest.fixture
def sap_agent(request, monkeypatch):
    monkeypatch.setenv("sap_agent", getattr(request, "param", "true"))


# https://github.com/vhostmd/vhostmd/blob/52b2dbf5c7136f87b1e6a6f4a10c363779bd1fb4/README#L264
def check_vbd_device(devices, vbd_path):
    for disk in devices.getElementsByTagName("disk"):
        source = disk.getElementsByTagName("source")[0]
        if source.getAttribute("file") == vbd_path:
            vbd_device = disk
            break
    else:
        assert not "Injected vbd device not found in dom xml"

    assert len(vbd_device.getElementsByTagName("readonly")) == 1


# https://github.com/vhostmd/vhostmd/blob/52b2dbf5c7136f87b1e6a6f4a10c363779bd1fb4/README#L293
def check_virtio_device(devices):
    for channel in devices.getElementsByTagName("channel"):
        target = channel.getElementsByTagName("target")[0]
        if target.getAttribute("name") == vhostmd_before.VIRTIO_CHANNEL_NAME:
            virtio_device = channel
            break
    else:
        assert not "Injected virtio device not found in dom xml"

    assert virtio_device.getAttribute("type") == "unix"
    assert target.getAttribute("type") == "virtio"

    source = virtio_device.getElementsByTagName("source")[0]
    assert source.getAttribute("mode") == "bind"


def test_hook_should_inject_devices_to_vm(dom_xml, vhostmd_conf, sap_agent):
    vhostmd_before.main(vhostmd_conf)
    xml_doc = hooking.read_domxml()
    devices = xml_doc.getElementsByTagName("devices")[0]

    check_vbd_device(devices, vhostmd_before.DEFAULT_VBD_PATH)
    check_virtio_device(devices)


@pytest.mark.parametrize("sap_agent", indirect=True, argvalues=["false"])
def test_vm_with_sap_agent_disabled_should_be_unaffected(dom_xml, vhostmd_conf,
                                                         sap_agent):
    dom_xml_before = hooking.read_domxml().toxml()
    vhostmd_before.main(vhostmd_conf)

    assert hooking.read_domxml().toxml() == dom_xml_before
