# Copyright 2013-2019 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

from contextlib import contextmanager
import logging
import os
import shlex
import threading

import pyinotify

from vdsm.common import fileutils
from vdsm.common.constants import P_VDSM_RUN
from vdsm.common import logutils
from vdsm.network import ifacetracking
from vdsm.network.kernelconfig import networks_northbound_ifaces


MONITOR_FOLDER = os.path.join(P_VDSM_RUN, 'dhclientmon')
MONITOR_STOP_SIG_FILE = os.path.join(
    MONITOR_FOLDER, 'dhclient-stop-monitoring'
)

_action_handler_db = []


class ActionType(object):
    CONFIGURE = 'configure'
    REMOVE = 'remove'


class ResponseField(object):
    ACTION = 'action'
    IPADDR = 'ip'
    IPMASK = 'mask'
    IPROUTE = 'route'
    IFACE = 'iface'


def start():
    thread = threading.Thread(
        target=_monitor_dhcp_responses, name='dhclient-monitor'
    )
    thread.daemon = True
    thread.start()
    return thread


def stop():
    fileutils.touch_file(MONITOR_STOP_SIG_FILE)


def wait(thread):
    thread.join()


@contextmanager
def dhclient_monitor_ctx():
    thread = start()
    try:
        yield
    finally:
        stop()
        wait(thread)


def register_action_handler(action_type, action_function, required_fields):
    """
    Register an action, which is to be executed when a dhcp response is
    accepted with the matching action type and the required data fields.

    The only action type supported for the moment is ActionType.CONFIGURE
    The default action is REMOVE.

    The action function provided is called with the required fields as kwargs.

    The required_fields is a tuple of all the fields that must exist for the
    action handler to be executed.
    The action_function must include all specified fields with exact naming.
    """
    _action_handler_db.append((action_type, action_function, required_fields))


class DHClientEventHandler(pyinotify.ProcessEvent):
    _stop = False

    def process_IN_CLOSE_WRITE(self, event):
        _dhcp_response_handler(event.pathname)
        if MONITOR_STOP_SIG_FILE in event.pathname:
            self.set_stop_notifier()

    def set_stop_notifier(self):
        self._stop = True

    def get_stop_notifier(self, *args):
        return self._stop


def _dhcp_response_handler(data_filepath):
    with _cleaning_file(data_filepath):
        dhcp_response = _dhcp_response_data(data_filepath)

        action = dhcp_response.get(ResponseField.ACTION)
        device = dhcp_response.get(ResponseField.IFACE)

        if device is None:
            logging.warning('DHCP response with no device')
            return

        logging.info('Received DHCP response: %s', dhcp_response)

        if _is_vdsm_interface(device):
            _process_dhcp_response_actions(action, dhcp_response)
        else:
            logging.info('Interface %s is not a libvirt interface', device)

        ifacetracking.remove(device)


@contextmanager
def _cleaning_file(filepath):
    try:
        yield
    finally:
        os.remove(filepath)


def _process_dhcp_response_actions(action, dhcp_response):
    _normalize_response(dhcp_response)

    for action_handler in _action_handlers(action):
        _, action_function, required_fields = action_handler
        if set(required_fields) <= set(dhcp_response):
            fields = {k: dhcp_response[k] for k in required_fields}
            action_function(**fields)


def _action_handlers(action_type):
    return (ah for ah in _action_handler_db if ah[0] == action_type)


@logutils.traceback()
def _monitor_dhcp_responses():
    logging.debug('Starting to monitor dhcp responses')

    # Subscribe to pyinotify event
    watchManager = pyinotify.WatchManager()
    handler = DHClientEventHandler()
    notifier = pyinotify.Notifier(watchManager, handler)
    # pylint: disable=no-member
    watchManager.add_watch(MONITOR_FOLDER, pyinotify.IN_CLOSE_WRITE)

    # Run once manually in case dhclient operated while supervdsm was down
    # Run sorted so that if multiple files exist for an interface, we'll
    # execute them alphabetically and thus according to their time stamp
    for filePath in sorted(os.listdir(MONITOR_FOLDER)):
        _dhcp_response_handler(MONITOR_FOLDER + '/' + filePath)

    notifier.loop(handler.get_stop_notifier)


def _dhcp_response_data(fpath):
    data = {}
    try:
        with open(fpath) as f:
            for line in shlex.split(f):
                k, v = line.split('=', 1)
                data[k] = v
    except:
        logging.exception('Error reading dhcp response file {}'.format(fpath))
    return data


def _is_vdsm_interface(device_name):
    if ifacetracking.is_tracked(device_name):
        return True
    else:
        return device_name in networks_northbound_ifaces()


def _normalize_response(response):
    # A zeroed route field is equivalent to no route field.
    route_field = ResponseField.IPROUTE
    if route_field in response and response[route_field] == '0.0.0.0':
        response.pop(ResponseField.IPROUTE)
