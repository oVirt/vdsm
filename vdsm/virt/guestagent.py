#
# Copyright 2011-2014 Red Hat, Inc.
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

import array
import logging
import time
import socket
import errno
import json
import unicodedata

# TODO: in future import from ..
import supervdsm
from vdsm.infra import filecontrol

from . import vmstatus

_MAX_SUPPORTED_API_VERSION = 2
_IMPLICIT_API_VERSION_ZERO = 0

_MESSAGE_API_VERSION_LOOKUP = {
    'set-number-of-cpus': 1}

_REPLACEMENT_CHAR = u'\ufffd'
_RESTRICTED_CHARS = frozenset(unichr(c) for c in
                              list(range(8 + 1)) +
                              list(range(0xB, 0xC + 1)) +
                              list(range(0xE, 0x1F + 1)) +
                              list(range(0x7F, 0x84 + 1)) +
                              list(range(0x86, 0x9F + 1)) +
                              [0xFFFE, 0xFFFF])


def _filterXmlChars(u):
    """
    The set of characters allowed in XML documents is described in
    http://www.w3.org/TR/xml11/#charsets

    "Char" is defined as any unicode character except the surrogate blocks,
    \ufffe and \uffff.
    "RestrictedChar" is defiend as the code points in _RESTRICTED_CHARS above

    It's a little hard to follow, but the upshot is an XML document
    must contain only characters in Char that are not in
    RestrictedChar.

    Note that Python's xmlcharrefreplace option is not relevant here -
    that's about handling characters which can't be encoded in a given
    charset encoding, not which aren't permitted in XML.
    """

    if not isinstance(u, unicode):
        raise TypeError

    chars = array.array('u', u)
    for i, c in enumerate(chars):
        if (c > u'\U00010fff' or unicodedata.category(c) == 'Cs'
                or c in _RESTRICTED_CHARS):
            chars[i] = _REPLACEMENT_CHAR
    return chars.tounicode()


def _filterObject(obj):
    """
    Apply _filterXmlChars on every string in the json response object
    """
    def filt(o):
        if isinstance(o, dict):
            return dict(map(filt, o.iteritems()))
        elif isinstance(o, list):
            return map(filt, o)
        elif isinstance(o, tuple):
            return tuple(map(filt, o))
        elif isinstance(o, basestring):
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


class GuestAgent(object):
    MAX_MESSAGE_SIZE = 2 ** 20  # 1 MiB for now

    def __init__(self, socketName, channelListener, log, onStatusChange,
                 user='Unknown', ips=''):
        self.effectiveApiVersion = _IMPLICIT_API_VERSION_ZERO
        self._onStatusChange = onStatusChange
        self.log = log
        self._socketName = socketName
        self._sock = _create_socket()
        self._stopped = True
        self._status = None
        self.guestDiskMapping = {}
        self.guestInfo = {
            'username': user,
            'memUsage': 0,
            'guestCPUCount': -1,
            'guestIPs': ips,
            'guestFQDN': '',
            'session': 'Unknown',
            'appsList': [],
            'disksUsage': [],
            'netIfaces': [],
            'memoryStats': {}}
        self._agentTimestamp = 0
        self._channelListener = channelListener
        self._messageState = MessageState.NORMAL

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
        self._diskMappingHash = hash(json.dumps(value, sort_keys=True))

    @property
    def diskMappingHash(self):
        return self._diskMappingHash

    def connect(self):
        self._prepare_socket()
        self._channelListener.register(
            self._create,
            self._connect,
            self._onChannelRead,
            self._onChannelTimeout,
            self)

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
            self.effectiveApiVersion = version
            if commonVersion != _IMPLICIT_API_VERSION_ZERO:
                # Only notify the guest agent if the API was not disabled
                self._forward('api-version', {'apiVersion': commonVersion})

    def _prepare_socket(self):
        supervdsm.getProxy().prepareVmChannel(self._socketName)

    @staticmethod
    def _create(self):
        self._sock.close()
        self._sock = _create_socket()
        return self._sock.fileno()

    @staticmethod
    def _connect(self):
        ret = False
        try:
            self._stopped = True
            self.log.debug("Attempting connection to %s", self._socketName)
            result = self._sock.connect_ex(self._socketName)
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
        args['__name__'] = cmd
        message = (json.dumps(args) + '\n').encode('utf8')
        self._sock.send(message)
        self.log.log(logging.TRACE, 'sent %s', message)

    def _handleMessage(self, message, args):
        self.log.log(logging.TRACE, "Guest's message %s: %s", message, args)
        if message == 'heartbeat':
            self.guestInfo['memUsage'] = int(args['free-ram'])
            # ovirt-guest-agent reports the following fields in 'memory-stat':
            # 'mem_total', 'mem_free', 'mem_unused', 'swap_in', 'swap_out',
            # 'pageflt' and 'majflt'
            if 'memory-stat' in args:
                for (k, v) in args['memory-stat'].iteritems():
                    # Convert the value to string since 64-bit integer is not
                    # supported in XMLRPC
                    self.guestInfo['memoryStats'][k] = str(v)

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
            self.guestInfo['appsList'] = args['applications']
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
            self.log.debug("RHEV agent was uninstalled.")
            self.guestInfo['appsList'] = []
        elif message == 'session-startup':
            self.log.debug("Guest system is started or restarted.")
        elif message == 'fqdn':
            self.guestInfo['guestFQDN'] = args['fqdn']
        elif message == 'session-shutdown':
            self.log.debug("Guest system shuts down.")
        elif message == 'disks-usage':
            disks = []
            for disk in args['disks']:
                # Converting to string because XML-RPC doesn't support 64-bit
                # integers.
                disk['total'] = str(disk['total'])
                disk['used'] = str(disk['used'])
                disks.append(disk)
            self.guestInfo['disksUsage'] = disks
            self.guestDiskMapping = args.get('mapping', {})
        elif message == 'number-of-cpus':
            self.guestInfo['guestCPUCount'] = int(args['count'])
        else:
            self.log.error('Unknown message type %s', message)

    def stop(self):
        self._stopped = True
        try:
            self._channelListener.unregister(self._sock.fileno())
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
        if self.isResponsive():
            # Returning deep copy would be safer but could have performance
            # implications (e.g. on lists of thousands installed Windows
            # packages).
            return self.guestInfo.copy()
        else:
            return {
                'username': 'Unknown',
                'session': 'Unknown',
                'memUsage': 0,
                'guestCPUCount': -1,
                'appsList': self.guestInfo['appsList'],
                'guestIPs': self.guestInfo['guestIPs'],
                'guestFQDN': self.guestInfo['guestFQDN']}

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

    @staticmethod
    def _onChannelTimeout(self):
        self.guestInfo['memUsage'] = 0
        if self.guestStatus not in (vmstatus.POWERING_DOWN,
                                    vmstatus.REBOOT_IN_PROGRESS):
            self.log.log(logging.TRACE, "Guest connection timed out")
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
        while (not self._stopped) and '\n' in data:
            line, data = data.split('\n', 1)
            line = ''.join(self._buffer) + line
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

    @staticmethod
    def _onChannelRead(self):
        result = True
        try:
            while not self._stopped:
                data = self._sock.recv(2 ** 16)
                # The connection is broken when recv returns no data
                # therefore we're going to set ourself to stopped state
                if not data:
                    self._stopped = True
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
