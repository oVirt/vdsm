# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import json
import os
import sys


def fake_error(res):
    if res == "FAIL_JSON":
        return object
    elif res == "FAIL":
        sys.exit(1)
    elif res == "RAISE":
        raise RuntimeError("error message from os_brick")


def get_connector_properties(*args, **kwargs):
    res = os.environ.get("FAKE_CONNECTOR_INFO_RESULT", None)
    if res == "OK" or res is None:
        return {"multipath": True}
    else:
        return fake_error(res)


class InitiatorConnector(object):

    @staticmethod
    def factory(*args, **kwargs):
        return FakeConnector()


class FakeConnector(object):

    def connect_volume(self, connection_properties):
        log_action("connect_volume", connection_properties)
        res = os.environ["FAKE_ATTACH_RESULT"]
        if res == "OK":
            return {"path": "/dev/fakesda",
                    "scsi_wwn": "fakewwn",
                    "multipath_id": "fakemultipathid"}
        elif res == "OK_RBD":
            return {"path": "/dev/fakerbd"}
        elif res == "NO_WWN":
            return {"path": "/dev/fakesda"}
        else:
            return fake_error(res)

    def disconnect_volume(self, connection_properties, device_info,
                          force=False, ignore_errors=False):
        log_action("disconnect_volume",
                   connection_properties, device_info, force, ignore_errors)


def log_action(action, *args):
    log_path = os.environ["FAKE_OS_BRICK_LOG"]
    entry = {"action": action, "arguments": args}
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")
