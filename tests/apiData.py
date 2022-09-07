# SPDX-FileCopyrightText: 2012 Adam Litke, IBM Corporation
# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division


class APIData(object):
    def __init__(self, obj, meth, data):
        self.obj = obj
        self.meth = meth
        self.data = data


testPing_apidata = [
    APIData('Global', 'ping', {
        'status': {'code': 0, 'message': 'OK'}})
]

testPingError_apidata = [
    APIData('Global', 'ping', {
        'status': {'code': 1, 'message': 'Fake error'}})
]
