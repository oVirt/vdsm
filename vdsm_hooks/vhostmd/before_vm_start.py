#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

import enum
import os.path
import subprocess

from xml.dom import minidom

import hooking

from vdsm.common import constants


VHOSTMD_CONF_PATH = os.path.join(constants.SYSCONF_PATH,
                                 "vhostmd/vhostmd.conf")
DEFAULT_VBD_PATH = "/dev/shm/vhostmd0"
VIRTIO_CHANNEL_NAME = "org.github.vhostmd.1"


class VhostmdTransport(enum.Enum):
    VBD = "vbd"
    VIRTIO = "virtio"


class VhostmdConf():

    def __init__(self, path=VHOSTMD_CONF_PATH):
        dom = minidom.parse(path)
        vhostmd = dom.getElementsByTagName("vhostmd")[0]
        globals_ = vhostmd.getElementsByTagName("globals")[0]

        try:
            disk = globals_.getElementsByTagName("disk")[0]
            path = disk.getElementsByTagName("path")[0]
            self._vbd_path = path.childNodes[0].data
        except IndexError:
            self._vbd_path = DEFAULT_VBD_PATH

        self._transports = set(
            VhostmdTransport(t.childNodes[0].data)
            for t in globals_.getElementsByTagName("transport")
        )

    @property
    def vbd_path(self):
        return self._vbd_path

    @property
    def transports(self):
        return self._transports


# https://github.com/vhostmd/vhostmd/blob/52b2dbf5c7136f87b1e6a6f4a10c363779bd1fb4/README#L264
def add_vbd_device(domxml, vbd_path):
    devices = domxml.getElementsByTagName('devices')[0]

    disk_doc = minidom.parseString(
        """
        <disk type='file' device='disk'>
            <source file='{}'/>
            <target dev='vdzz' bus='virtio'/>
            <readonly/>
        </disk>
        """.format(vbd_path)
    )

    disk = disk_doc.getElementsByTagName('disk')[0]
    devices.appendChild(disk)


# https://github.com/vhostmd/vhostmd/blob/52b2dbf5c7136f87b1e6a6f4a10c363779bd1fb4/README#L293
def add_virtio_device(domxml):
    devices = domxml.getElementsByTagName('devices')[0]

    virtio_doc = minidom.parseString(
        """
        <channel type='unix'>
            <source mode='bind'/>
            <target type='virtio' name='{}'/>
        </channel>
        """.format(VIRTIO_CHANNEL_NAME)
    )

    channel = virtio_doc.getElementsByTagName("channel")[0]
    devices.appendChild(channel)


def main(vhostmd_conf):
    if hooking.tobool(os.environ.get("sap_agent", False)):
        domxml = hooking.read_domxml()

        subprocess.call(["/usr/bin/sudo", "-n", "/sbin/service", "vhostmd",
                         "start"])

        if VhostmdTransport.VBD in vhostmd_conf.transports:
            add_vbd_device(domxml, vhostmd_conf.vbd_path)

        if VhostmdTransport.VIRTIO in vhostmd_conf.transports:
            add_virtio_device(domxml)

        hooking.write_domxml(domxml)


if __name__ == "__main__":
    main(VhostmdConf())
