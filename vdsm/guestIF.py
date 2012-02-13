#
# Copyright 2011 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

import traceback, logging, threading
import time
import socket
import json
from config import config
import utils
import constants

__RESTRICTED_CHARS = set(range(8+1)).union(
    set(range(0xB,0xC+1))).union(
    set(range(0xE,0x1F+1))).union(
    set(range(0x7F,0x84+1))).union(
    set(range(0x86,0x9F+1)))

def _filterXmlChars(u):
    """
    Filter out restarted xml chars from unicode string. Not using
    Python's xmlcharrefreplace because it accepts '\x01', which
    the spec frown upon.

    Set taken from http://www.w3.org/TR/xml11/#NT-RestrictedChar
    """

    def maskRestricted(c):
        if ord(c) in __RESTRICTED_CHARS: return '?'
        else: return c

    return ''.join(maskRestricted(c) for c in u)

class GuestAgent (threading.Thread):
    def __init__(self, socketName, log, user='Unknown', ips='', connect=True):
        self.log = log
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socketName = socketName
        self._stopped = True
        self.guestStatus = None
        self.guestInfo = {'username': user, 'memUsage': 0, 'guestIPs': ips,
                          'session': 'Unknown', 'appsList': [], 'disksUsage': [],
                          'netIfaces': []}
        self._agentTimestamp = 0

        threading.Thread.__init__(self)
        if connect:
            self.start()

    def _connect(self):
        logging.debug("Attempting connection to %s", self._socketName)
        utils.execCmd([constants.EXT_PREPARE_VMCHANNEL, self._socketName],
                sudo=True)
        self._sock.settimeout(5)
        while not self._stopped:
            try:
                self._sock.connect(self._socketName)
                logging.debug("connected to %s", self._socketName)
                self._sock.settimeout(config.getint('vars', 'guest_agent_timeout'))
                return True
            except:
                time.sleep(1)
        return False

    def _forward(self, cmd, args = {}):
        args['__name__'] = cmd
        message = (json.dumps(args) + '\n').encode('utf8')
        self._sock.send(message)
        self.log.log(logging.TRACE, 'sent %s', message)

    def _handleMessage(self, message, args):
        self.log.log(logging.TRACE, "Guest's message %s: %s", message, args)
        if self.guestStatus == None:
            self.guestStatus = 'Running'
        if message == 'heartbeat':
            self.guestStatus = 'Running'
            self.guestInfo['memUsage'] = int(args['free-ram'])
        elif message == 'host-name':
            self.guestInfo['guestName'] = _filterXmlChars(args['name'])
        elif message == 'os-version':
            self.guestInfo['guestOs'] = _filterXmlChars(args['version'])
        elif message == 'network-interfaces':
            interfaces = []
            old_ips = ''
            for iface in args['interfaces']:
                iface['name'] = _filterXmlChars(iface['name'])
                iface['hw'] = _filterXmlChars(iface['hw'])
                iface['inet'] = map(_filterXmlChars, iface['inet'])
                iface['inet6'] = map(_filterXmlChars, iface['inet6'])
                interfaces.append(iface)
                # Provide the old information which includes only the IP addresses.
                old_ips += ' '.join(iface['inet']) + ' '
            self.guestInfo['netIfaces'] = interfaces
            self.guestInfo['guestIPs'] = old_ips.strip()
        elif message == 'applications':
            self.guestInfo['appsList'] = map(_filterXmlChars, args['applications'])
        elif message == 'active-user':
            currentUser = _filterXmlChars(args['name'])
            if (currentUser != self.guestInfo['username']) and not (currentUser=='Unknown' and self.guestInfo['username']=='None'):
                self.guestInfo['username'] = currentUser
                self.guestInfo['lastLogin'] = time.time()
            self.log.debug(repr(self.guestInfo['username']))
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
        elif message == 'session-shutdown':
            self.log.debug("Guest system shuts down.")
        elif message == 'disks-usage':
            disks = []
            for disk in args['disks']:
                disk['path'] = _filterXmlChars(disk['path'])
                disk['fs'] = _filterXmlChars(disk['fs'])
                # Converting to string because XML-RPC doesn't support 64-bit
                # integers.
                disk['total'] = _filterXmlChars(str(disk['total']))
                disk['used'] = _filterXmlChars(str(disk['used']))
                disks.append(disk)
            self.guestInfo['disksUsage'] = disks
        else:
            self.log.error('Unknown message type %s', message)

    def stop (self):
        self._stopped = True
        self._sock.close()

    def isResponsive (self):
        return time.time() - self._agentTimestamp < 120

    def getStatus (self):
        return self.guestStatus

    def getGuestInfo (self):
        if self.isResponsive():
            return self.guestInfo
        else:
            return {'username': 'Unknown',
                 'session': 'Unknown', 'memUsage': 0,
                 'appsList': self.guestInfo['appsList'],
                 'guestIPs': self.guestInfo['guestIPs']}

    def onReboot (self):
        self.guestStatus = 'RebootInProgress'
        self.guestInfo['lastUser'] = '' + self.guestInfo['username']
        self.guestInfo['username'] = 'Unknown'
        self.guestInfo['lastLogout'] = time.time()

    def desktopLock(self):
        try:
            self.log.debug("desktopLock called")
            self._forward("lock-screen")
        except:
            self.log.error(traceback.format_exc())

    def desktopLogin (self, domain, user, password):
        try:
            self.log.debug("desktopLogin called")
            if domain != '':
                username = user + '@' + domain
            else:
                username = user
            self._forward('login', { 'username' : username, "password" : password })
        except:
            self.log.error(traceback.format_exc())

    def desktopLogoff (self, force):
        try:
            self.log.debug("desktopLogoff called")
            self._forward('log-off')
        except:
            self.log.error(traceback.format_exc())

    def desktopShutdown (self, timeout, msg):
        try:
            self.log.debug("desktopShutdown called")
            self._forward('shutdown', { 'timeout' : timeout, 'message' : msg })
        except:
            self.log.error(traceback.format_exc())

    def sendHcCmdToDesktop (self, cmd):
        try:
            self.log.debug("sendHcCmdToDesktop('%s')"%(cmd))
            self._forward(str(cmd))
        except:
            self.log.error(traceback.format_exc())

    def _onChannelTimeout(self):
        self.guestInfo['memUsage'] = 0
        if self.guestStatus not in ("Powered down", "RebootInProgress"):
            self.log.log(logging.TRACE, "Guest connection timed out")
            self.guestStatus = None

    READSIZE = 2**16
    def _readBuffer(self):
        try:
            s = self._sock.recv(self.READSIZE)
            if s:
                self._buffer += s
            else:
                # s is None if recv() exit with an error. The sleep() is
                # called in order to prevent a 100% CPU utilization while
                # trying to read from the socket again (and again...).
                time.sleep(1)
        except socket.timeout:
            self._onChannelTimeout()

    def _readLine(self):
        newline = self._buffer.find('\n')
        while not self._stopped and newline < 0:
            self._readBuffer()
            newline = self._buffer.find('\n')
        if newline >= 0:
            line, self._buffer = self._buffer.split('\n', 1)
        else:
            line = None
        return line

    def _parseLine(self, line):
        args = json.loads(line.decode('utf8'))
        name = args['__name__']
        del args['__name__']
        return (name, args)

    def run(self):
        self._stopped = False
        try:
            if not self._connect():
                return
            self._forward('refresh')
            self._buffer = ''
            while not self._stopped:
                line = None
                try:
                    line = self._readLine()
                    # line is always None after stop() is called and the
                    # socket is closed.
                    if line:
                        (message, args) = self._parseLine(line)
                        self._agentTimestamp = time.time()
                        self._handleMessage(message, args)
                except:
                    self.log.exception("Run exception: %s", line)
        except:
            if not self._stopped:
                self.log.error("Unexpected exception: " + traceback.format_exc())
