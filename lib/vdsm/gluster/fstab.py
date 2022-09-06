# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import logging
import os
from collections import namedtuple

from vdsm.gluster import exception as ge

from . import safeWrite


log = logging.getLogger("Gluster")
FstabRecord = namedtuple("FstabRecord", "device, mountPoint, fsType, "
                         "mntOpts, fsDump, fsPass")


class FsTab(object):
    def __init__(self, fileName="/etc/fstab"):
        self.fileName = fileName

    def _list(self):
        devList = []
        with open(self.fileName, "r") as f:
            for line in f:
                line = line.strip()
                if not (line == '' or line.startswith("#")):
                    tokens = line.split()
                    devList.append(FstabRecord(tokens[0], tokens[1],
                                               tokens[2],
                                               tokens[3].split(","),
                                               int(tokens[4]),
                                               int(tokens[5])))
        return devList

    def _getFsUuid(self, device):
        for uuid in os.listdir("/dev/disk/by-uuid"):
            if device == os.path.realpath("/dev/disk/by-uuid/%s" % uuid):
                return uuid
        return None

    def _exists(self, device):
        uuid = "UUID=%s" % (self._getFsUuid(device))
        for dev in self._list():
            if device == dev.device or uuid == dev.device:
                return True
        return False

    def add(self, device, mountPoint, fsType,
            mntOpts=['defaults'], fsDump=0, fsPass=0):
        if self._exists(device):
            raise ge.GlusterHostStorageDeviceFsTabFoundException(device)
        uuid = self._getFsUuid(device)
        if not uuid:
            log.warn("UUID not found for device %s" % device)
        content = open(self.fileName).read()
        content += "%s%s\t%s\t%s\t%s\t%s\t%s\n" % (
            '' if content.endswith('\n') else '\n',
            "UUID=%s" % uuid if uuid else device,
            mountPoint, fsType, ",".join(mntOpts), fsDump, fsPass)
        safeWrite(self.fileName, content)
