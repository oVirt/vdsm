#
# Copyright 2011-2019 Red Hat, Inc.
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

from __future__ import absolute_import
from __future__ import division

import contextlib
import time
import socket
import errno
import json
import re
import threading
import uuid
import weakref

import six

from vdsm import utils
from vdsm.common import filecontrol
from vdsm.common import supervdsm
from vdsm.common.units import MiB
from vdsm.virt import vmstatus

_MAX_SUPPORTED_API_VERSION = 3
_IMPLICIT_API_VERSION_ZERO = 0
_REPLY_CAP_MIN_VERSION = 3

_MESSAGE_API_VERSION_LOOKUP = {
    'set-number-of-cpus': 1,
    'lifecycle-event': 3}

_REPLACEMENT_CHAR = u'\ufffd'

# The set of characters allowed in XML documents is described in
# http://www.w3.org/TR/xml11/#charsets
#
# Char is defined as any Unicode character, excluding the surrogate blocks,
# FFFE, and FFFF:
#
#     [#x1-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF]
#
# But according to bug 606281, we should also avoid RestrictedChar character
# ranges:
#
#     [#x1-#x8] | [#xB-#xC] | [#xE-#x1F] | [#x7F-#x84] | [#x86-#x9F]
#
# The following ranges are the results of substructing the RestrictedChar
# ranges from Char ranges, and adding 0x00, FFFE, and FFFF. Any character in
# these ranges will be replaced by the unicode replacement character.
#
# Note that Python unicode string cannot represent code points above 0x10FFFF,
# so we don't need to filter anything above this value.

_FILTERED_CHARS = (
    u"\u0000-\u0008"
    u"\u000b-\u000c"
    u"\u000e-\u001f"
    u"\u007f-\u0084"
    u"\u0086-\u009f"
    u"\ud800-\udfff"
    u"\ufffe-\uffff"
)

_filter_chars_re = re.compile(u'[%s]' % _FILTERED_CHARS)
_qga_re = re.compile(r'\bqemu[ -](guest[ -]agent|ga)\b', re.IGNORECASE)


def _filterXmlChars(u):
    if not isinstance(u, six.text_type):
        raise TypeError
    return _filter_chars_re.sub(_REPLACEMENT_CHAR, u)


def _filterObject(obj):
    """
    Apply _filterXmlChars on every string in the json response object
    """
    def filt(o):
        if isinstance(o, dict):
            return {filt(k): filt(v) for k, v in six.iteritems(o)}
        elif isinstance(o, list):
            return [filt(i) for i in o]
        elif isinstance(o, six.text_type):
            return _filterXmlChars(o)
        return o
    return filt(obj)


def _create_socket():
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    filecontrol.set_close_on_exec(sock.fileno())
    sock.setblocking(0)
    return sock


class MessageState:
    NORMAL = 'normal'
    TOO_BIG = 'too-big'


class GuestAgentUnsupportedMessage(Exception):
    def __init__(self, cmd, requiredVersion, currentVersion):
        message = "Guest Agent command '%s' requires version '%d'. Current " \
                  "version is '%d'" % (cmd, requiredVersion, currentVersion)
        Exception.__init__(self, message)


class GuestAgentEvents(object):
    def __init__(self, agent):
        self._agent = weakref.ref(agent)

    def _send(self, *args, **kwargs):
        self._agent().send_lifecycle_event(*args, **kwargs)

    def before_hibernation(self, wait_timeout=None):
        reply_id = str(uuid.uuid4())
        with self._agent()._waitable_message(wait_timeout, reply_id):
            self._send('before_hibernation', reply_id=reply_id)

    def after_hibernation_failure(self):
        self._send('after_hibernation', failure=True)

    def after_hibernation(self):
        self._send('after_hibernation')

    def before_migration(self, wait_timeout=None):
        reply_id = str(uuid.uuid4())
        with self._agent()._waitable_message(wait_timeout, reply_id):
            self._send('before_migration', reply_id=reply_id)

    def after_migration_failure(self):
        self._send('after_migration', failure=True)

    def after_migration(self):
        self._send('after_migration')


class GuestAgent(object):
    MAX_MESSAGE_SIZE = 1 * MiB  # for now

    def __init__(self, socketName, channelListener, log, onStatusChange,
                 qgaCaps, qgaGuestInfo, api_version=None, user='Unknown',
                 ips=''):
        self.effectiveApiVersion = min(
            api_version or _IMPLICIT_API_VERSION_ZERO,
            _MAX_SUPPORTED_API_VERSION)
        self._onStatusChange = onStatusChange
        self.log = log
        self._socketName = socketName
        self._sock = _create_socket()
        self._stopped = True
        self._status = None
        self.guestDiskMapping = {}
        self.oVirtGuestDiskMapping = {}
        self.guestInfo = {
            'username': user,
            'memUsage': 0,
            'guestCPUCount': -1,
            'guestIPs': ips,
            'guestFQDN': '',
            'session': 'Unknown',
            'appsList': (),
            'disksUsage': [],
            'netIfaces': [],
            'memoryStats': {}}
        self._agentTimestamp = 0
        self._channelListener = channelListener
        self._messageState = MessageState.NORMAL
        self.events = GuestAgentEvents(self)
        self._completion_lock = threading.Lock()
        self._completion_events = {}
        self._first_connect = threading.Event()
        self._qgaCaps = qgaCaps
        self._qgaGuestInfo = qgaGuestInfo

    def _on_completion(self, reply_id):
        with self._completion_lock:
            event = self._completion_events.pop(reply_id, None)
        if event is not None:
            event.set()

    @property
    def can_reply(self):
        active = self.isResponsive()
        return active and self.effectiveApiVersion >= _REPLY_CAP_MIN_VERSION

    @contextlib.contextmanager
    def _waitable_message(self, wait_timeout, reply_id):
        if self.can_reply and wait_timeout is not None:
            event = threading.Event()
            with self._completion_lock:
                self._completion_events[reply_id] = event
            yield
            event.wait(wait_timeout)
            with self._completion_lock:
                self._completion_events.pop(reply_id, None)
        else:
            yield

    @property
    def guestStatus(self):
        return self._status

    @guestStatus.setter
    def guestStatus(self, value):
        oldValue = self._status
        self._status = value
        if oldValue != value and self._onStatusChange:
            self._onStatusChange()

    @property
    def guestDiskMapping(self):
        return self._guestDiskMapping

    @guestDiskMapping.setter
    def guestDiskMapping(self, value):
        self._guestDiskMapping = value
        if value:
            self._diskMappingHash = hash(json.dumps(value, sort_keys=True))
        else:
            self._diskMappingHash = None

    @property
    def diskMappingHash(self):
        return self._diskMappingHash

    def start(self):
        self.log.info("Starting connection")
        self._prepare_socket()
        self._channelListener.register(
            self._create,
            self._connect,
            self._onChannelRead,
            self._onChannelTimeout)

    def _handleAPIVersion(self, version):
        """ Handles the API version value from the heartbeat

            If the value `version` is an valid int the highest possible
            API version in common will be determined and set to the
            attribute `self.effectiveApiVersion` if the value has changed. If
            the value changed the `api-version` message will  be sent to the
            guest agent to notify it about the changed common API version.

            If the value of `version` is not an int, the API version support
            will be disabled by assigning _IMPLICIT_API_VERSION_ZERO to
            `self.effectiveApiVersion`

        Args:
        version - the api version reported by the guest agent
        """
        try:
            commonVersion = int(version)
        except ValueError:
            self.log.warning("Received invalid version value: %s", version)
            commonVersion = _IMPLICIT_API_VERSION_ZERO
        else:
            commonVersion = max(commonVersion, _IMPLICIT_API_VERSION_ZERO)
            commonVersion = min(commonVersion, _MAX_SUPPORTED_API_VERSION)

        if commonVersion != self.effectiveApiVersion:
            # Only update if the value changed
            self.log.info("Guest API version changed from %d to %d",
                          self.effectiveApiVersion, commonVersion)
            self.effectiveApiVersion = commonVersion
            if commonVersion != _IMPLICIT_API_VERSION_ZERO:
                # Only notify the guest agent if the API was not disabled
                self._forward('api-version', {'apiVersion': commonVersion})

    def _prepare_socket(self):
        supervdsm.getProxy().prepareVmChannel(self._socketName)

    def _create(self):
        self._sock.close()
        self._sock = _create_socket()
        return self._sock.fileno()

    def _connect(self):
        ret = False
        try:
            self._stopped = True
            self.log.debug("Attempting connection to %s", self._socketName)
            result = self._sock.connect_ex(self._socketName)
            self._first_connect.set()
            if result == 0:
                self.log.debug("Connected to %s", self._socketName)
                self._messageState = MessageState.NORMAL
                self._clearReadBuffer()
                # Report the _MAX_SUPPORTED_API_VERSION on refresh to enable
                # the other side to see that we support API versioning
                self._forward('refresh',
                              {'apiVersion': _MAX_SUPPORTED_API_VERSION})
                self._stopped = False
                ret = True
            else:
                self.log.debug("Failed to connect to %s with %d",
                               self._socketName, result)
        except socket.error as err:
            self.log.debug("Connection attempt failed: %s", err)
        return ret

    def _forward(self, cmd, args={}):
        ver = _MESSAGE_API_VERSION_LOOKUP.get(cmd, _IMPLICIT_API_VERSION_ZERO)
        if ver > self.effectiveApiVersion:
            raise GuestAgentUnsupportedMessage(cmd, ver,
                                               self.effectiveApiVersion)
        self._first_connect.wait(self._channelListener.timeout())
        args['__name__'] = cmd
        # TODO: encoding is required only on Python 3. Replace with wrapper
        # hiding this difference.
        message = (json.dumps(args) + '\n').encode('utf8')
        # TODO: socket is non-blocking, handle possible EAGAIN
        self._sock.sendall(message)
        self.log.debug('sent %r', message)

    def _handleMessage(self, message, args):
        self.log.debug("Guest's message %s: %s", message, args)
        if message == 'heartbeat':
            self.guestInfo['memUsage'] = int(args['free-ram'])
            if 'memory-stat' in args:
                for k in ('mem_total', 'mem_unused', 'mem_buffers',
                          'mem_cached', 'swap_in', 'swap_out', 'pageflt',
                          'majflt'):
                    if k not in args['memory-stat']:
                        continue
                    # Convert the value to string since 64-bit integer is not
                    # supported in XMLRPC
                    self.guestInfo['memoryStats'][k] = str(
                        args['memory-stat'][k])
                    if k == 'mem_unused':
                        self.guestInfo['memoryStats']['mem_free'] = str(
                            args['memory-stat']['mem_unused'])

            if 'apiVersion' in args:
                # The guest agent supports API Versioning
                self._handleAPIVersion(args['apiVersion'])
            elif self.effectiveApiVersion != _IMPLICIT_API_VERSION_ZERO:
                # Older versions of the guest agent (before the introduction
                # of API versioning) do not report this field
                # Disable the API if not already disabled (e.g. after
                # downgrade of the guest agent)
                self.log.debug("API versioning no longer reported by guest.")
                self.effectiveApiVersion = _IMPLICIT_API_VERSION_ZERO
            # Only change the state AFTER all data of the heartbeat has been
            # consumed
            self.guestStatus = vmstatus.UP
        elif message == 'host-name':
            self.guestInfo['guestName'] = args['name']
        elif message == 'os-version':
            self.guestInfo['guestOs'] = args['version']
        elif message == 'os-info':
            self.guestInfo['guestOsInfo'] = args
        elif message == 'timezone':
            self.guestInfo['guestTimezone'] = args
        elif message == 'network-interfaces':
            interfaces = []
            old_ips = ''
            for iface in args['interfaces']:
                iface['inet'] = iface.get('inet', [])
                iface['inet6'] = iface.get('inet6', [])
                interfaces.append(iface)
                # Provide the old information which includes
                # only the IP addresses.
                old_ips += ' '.join(iface['inet']) + ' '
            self.guestInfo['netIfaces'] = interfaces
            self.guestInfo['guestIPs'] = old_ips.strip()
        elif message == 'applications':
            self.guestInfo['appsList'] = tuple(args['applications'])
            # Fake QEMU-GA if it is not reported
            if not any(bool(_qga_re.match(x))
                       for x in self.guestInfo['appsList']):
                qga_caps = self._qgaCaps()
                if qga_caps is not None and qga_caps['version'] is not None:
                    # NOTE: this is a tuple
                    self.guestInfo['appsList'] = \
                        self.guestInfo['appsList'] + \
                        ('qemu-guest-agent-%s' % qga_caps['version'],)
        elif message == 'active-user':
            currentUser = args['name']
            if ((currentUser != self.guestInfo['username']) and
                not (currentUser == 'Unknown' and
                     self.guestInfo['username'] == 'None')):
                self.guestInfo['username'] = currentUser
                self.guestInfo['lastLogin'] = time.time()
            self.log.debug("username: %s", repr(self.guestInfo['username']))
        elif message == 'session-logon':
            self.guestInfo['session'] = "UserLoggedOn"
        elif message == 'session-lock':
            self.guestInfo['session'] = "Locked"
        elif message == 'session-unlock':
            self.guestInfo['session'] = "Active"
        elif message == 'session-logoff':
            self.guestInfo['session'] = "LoggedOff"
        elif message == 'uninstalled':
            self.log.debug("guest agent was uninstalled.")
            self.guestInfo['appsList'] = ()
        elif message == 'session-startup':
            self.log.debug("Guest system is started or restarted.")
        elif message == 'fqdn':
            self.guestInfo['guestFQDN'] = args['fqdn']
        elif message == 'session-shutdown':
            self.log.debug("Guest system shuts down.")
        elif message == 'containers':
            self.guestInfo['guestContainers'] = args['list']
        elif message == 'disks-usage':
            disks = []
            for disk in args['disks']:
                # Converting to string because XML-RPC doesn't support 64-bit
                # integers.
                disk['total'] = str(disk['total'])
                disk['used'] = str(disk['used'])
                disks.append(disk)
            self.guestInfo['disksUsage'] = disks
            self.oVirtGuestDiskMapping = args.get('mapping', {})
        elif message == 'number-of-cpus':
            self.guestInfo['guestCPUCount'] = int(args['count'])
        elif message == 'completion':
            self._on_completion(args.pop('reply_id', None))
        else:
            self.log.error('Unknown message type %s', message)

    def stop(self):
        self.log.info("Stopping connection")
        self._stopped = True
        try:
            fileno = self._sock.fileno()
            if fileno >= 0:
                self._channelListener.unregister(fileno)
        except socket.error as e:
            if e.args[0] == errno.EBADF:
                # socket was already closed
                pass
            else:
                raise
        else:
            self._sock.close()

    def isResponsive(self):
        return time.time() - self._agentTimestamp < 120

    def getStatus(self):
        return self.guestStatus

    def getGuestInfo(self):
        # Prefer information from QEMU GA if available. Fall-back to oVirt GA
        # only for info that is not availble in QEMU GA.
        info = {
            'username': 'Unknown',
            'session': 'Unknown',
            'memUsage': 0,
            'guestCPUCount': -1,
            'appsList': (),
            'guestIPs': '',
            'guestFQDN': ''}
        diskMapping = {}
        if self.isResponsive():
            info.update(self.guestInfo)
            diskMapping.update(self.oVirtGuestDiskMapping)
        else:
            if len(self.guestInfo['appsList']) > 0:
                info['appsList'] = self.guestInfo['appsList']
            if len(self.guestInfo['guestIPs']) > 0:
                info['guestIPs'] = self.guestInfo['guestIPs']
            if len(self.guestInfo['guestFQDN']) > 0:
                info['guestFQDN'] = self.guestInfo['guestFQDN']
        qga = self._qgaGuestInfo()
        if qga is not None:
            if 'diskMapping' in qga:
                diskMapping.update(qga['diskMapping'])
                del qga['diskMapping']
            if len(info['appsList']) > 0 and 'appsList' in qga:
                # This is an exception since the entry from QEMU GA is faked.
                # Prefer oVirt GA info if available. Take fake QEMU GA info
                # only if the other is not available.
                del qga['appsList']
            info.update(qga)
        self.guestDiskMapping = diskMapping
        return utils.picklecopy(info)

    def onReboot(self):
        self.guestStatus = vmstatus.REBOOT_IN_PROGRESS
        self.guestInfo['lastUser'] = '' + self.guestInfo['username']
        self.guestInfo['username'] = 'Unknown'
        self.guestInfo['lastLogout'] = time.time()

    def desktopLock(self):
        try:
            self.log.debug("desktopLock called")
            self._forward("lock-screen")
        except Exception as e:
            if isinstance(e, socket.error) and e.args[0] == errno.EBADF:
                self.log.debug('desktopLock failed - Socket not connected')
                return  # Expected when not connected/closed socket
            self.log.exception("desktopLock failed with unexpected exception")

    def desktopLogin(self, domain, user, password):
        try:
            self.log.debug("desktopLogin called")
            if domain != '':
                username = user + '@' + domain
            else:
                username = user
            self._forward('login', {'username': username,
                                    "password": password.value})
        except:
            self.log.exception("desktopLogin failed")

    def desktopLogoff(self, force):
        try:
            self.log.debug("desktopLogoff called")
            self._forward('log-off')
        except:
            self.log.exception("desktopLogoff failed")

    def desktopShutdown(self, timeout, msg, reboot):
        try:
            self.log.debug("desktopShutdown called")
            self._forward('shutdown', {'timeout': timeout, 'message': msg,
                                       'reboot': str(reboot)})
        except:
            self.log.exception("desktopShutdown failed")

    def sendHcCmdToDesktop(self, cmd):
        try:
            self.log.debug("sendHcCmdToDesktop('%s')" % (cmd))
            self._forward(str(cmd))
        except:
            self.log.exception("sendHcCmdToDesktop failed")

    def setNumberOfCPUs(self, count):
        self.log.debug("setNumberOfCPUs('%d') called", count)
        self._forward('set-number-of-cpus', {'count': count})

    def send_lifecycle_event(self, event, **kwargs):
        self.log.debug('send_lifecycle_event %s called', event)
        try:
            message = {'type': event}
            message.update(kwargs)
            self._forward('lifecycle-event', message)
        except GuestAgentUnsupportedMessage:
            # This is ok, that guest agent doesn't know yet how to handle
            # the message
            pass
        except socket.error as e:
            self.log.debug("Failed to forward lifecycle-event: %s", e)

    def _onChannelTimeout(self):
        self.guestInfo['memUsage'] = 0
        if self.guestStatus not in (vmstatus.POWERING_DOWN,
                                    vmstatus.REBOOT_IN_PROGRESS):
            self.log.debug("Guest connection timed out")
            self.guestStatus = None

    def _clearReadBuffer(self):
        self._buffer = []
        self._bufferSize = 0

    def _processMessage(self, line):
        try:
            (message, args) = self._parseLine(line)
            self._agentTimestamp = time.time()
            self._handleMessage(message, args)
        except ValueError as err:
            self.log.error("%s: %s" % (err, repr(line)))

    def _handleData(self, data):
        while (not self._stopped) and b'\n' in data:
            line, data = data.split(b'\n', 1)
            line = b''.join(self._buffer) + line
            self._clearReadBuffer()
            if self._messageState is MessageState.TOO_BIG:
                self._messageState = MessageState.NORMAL
                self.log.warning("Not processing current message because it "
                                 "was too big")
            else:
                self._processMessage(line)

        self._buffer.append(data)
        self._bufferSize += len(data)

        if self._bufferSize >= self.MAX_MESSAGE_SIZE:
            self.log.warning("Discarding buffer with size: %d because the "
                             "message reached maximum size of %d bytes before "
                             "message end was reached.", self._bufferSize,
                             self.MAX_MESSAGE_SIZE)
            self._messageState = MessageState.TOO_BIG
            self._clearReadBuffer()

    def _onChannelRead(self):
        result = True
        try:
            while not self._stopped:
                data = self._sock.recv(2 ** 16)
                # The connection is broken when recv returns no data
                # therefore we're going to set ourself to stopped state
                if not data:
                    self._stopped = True
                    self.log.debug("Disconnected from %s", self._socketName)
                    result = False
                else:
                    self._handleData(data)
        except socket.error as err:
            if err.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                raise

        return result

    def _parseLine(self, line):
        # Deal with any bad UTF8 encoding from the (untrusted) guest,
        # by replacing them with the Unicode replacement character
        uniline = line.decode('utf8', 'replace')
        args = json.loads(uniline)
        # Filter out any characters in the untrusted guest response
        # that aren't permitted in XML.  This must be done _after_ the
        # JSON decoding, since otherwise JSON's \u escape decoding
        # could be used to generate the bad characters
        args = _filterObject(args)
        name = args['__name__']
        del args['__name__']
        return (name, args)
