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
import struct
from config import config
import utils
import constants

def _filterXmlChars(u):
    """
    Filter out restarted xml chars from unicode string

    Set taken from http://www.w3.org/TR/xml11/#NT-RestrictedChar
    """
    restricted = set(range(8+1)).union(
                 set(range(0xB,0xC+1))).union(
                 set(range(0xE,0x1F+1))).union(
                 set(range(0x7F,0x84+1))).union(
                 set(range(0x86,0x9F+1)))
    def maskRestricted(c):
        if ord(c) in restricted: return '?'
        else: return c

    return ''.join([maskRestricted(c) for c in u])


class guestMType:
    powerup=1
    powerdown=2
    heartbeat=3
    guestName=4
    guestOs=5
    IPAddresses=6
    lastSessionMessage=7
    userInfo=8
    newApp=9
    flushApps=10
    sessionLock=12
    sessionUnlock=13
    sessionLogoff=14
    sessionLogon=15
    agentCmd = 16 # obsolete
    agentUninstalled = 17
    sessionStartup = 18
    sessionShutdown = 19

class protocolMtype:
    register, unregister, forward = range(1, 4)
    error = 0x80000001


headerLength = 3
wordSize = 4
headerLengthBytes = headerLength * wordSize

class GuestAgent (threading.Thread):
    def __init__(self, socketName, log, user='Unknown', ips='', connect=True):
        self.log = log
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socketName = socketName
        self._stopped = True
        self.guestStatus = None
        self.guestInfo = {'username': user, 'memUsage': 0, 'guestIPs': ips,
                          'session': 'Unknown', 'appsList': []}
        self._agentTimestamp = 0

        # A temporary storage to hold the guest's application list during an
        # update from the guest. Will be obselete if a new virtual channel
        # (that can handle large messages) will be implemented and used.
        self._tempAppsList = []
        self._firstAppsList = True

        threading.Thread.__init__(self)
        if connect:
            self.start()

    def _connect(self):
        logging.debug("Attempting connection to %s", self._socketName)
        utils.execCmd([constants.EXT_PREPARE_VMCHANNEL, self._socketName])
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

    #The protocol envelope:
    #DWORD(32Bit) - ChannelID
    #DWORD(32Bit) - Action/Cmd
    #   1 - Register (to receive ChannelID events)
    #   2 - UnRegister (from ChannelID events)
    #   3 - Forward Message to channel
    #   0x80000001 - Error UnknownChannel
    #DWORD(32Bit) MessageLength(In Bytes)
    #message payload
    # Simple protocol at this point. Just a single byte
    # with enumeration of the event
    #   1 - PowerUp
    #   2 - PowerDown
    #   3 - HeartBeat

    CHANNEL = 1
    def _forward(self, s):
        lens = len(s)
        t = struct.pack('>III%ds' % lens, self.CHANNEL,
                    protocolMtype.forward, headerLengthBytes + lens, s)
        self._sock.send(t)
        self.log.log(logging.TRACE, 'sent %s', repr(t))

    def _parseHeader(self, header):
        channel, messageType, length = struct.unpack('>III', header)
        if channel != self.CHANNEL:
            self.log.error("Illegal channel id %d" % (channel))
            return 0
        if messageType != protocolMtype.forward:
            self.log.error("Unexpected message type " + str((channel, messageType, length)))
            return 0
        return length - headerLengthBytes

    def _parseBody(self, body):
        guestMessage, = struct.unpack('>I', body[:4])
        body = body[4:]
        self.log.log(logging.TRACE, 'guest message %s body %s',
                     guestMessage, body)
        if self.guestStatus == None:
            self.guestStatus = 'Running'
        if guestMessage == guestMType.heartbeat:
            self.guestStatus = 'Running'
            self.guestInfo['memUsage'] = int(body.strip())
        elif guestMessage == guestMType.powerup:
            self.guestStatus = 'Running'
        elif guestMessage == guestMType.powerdown:
            self.guestStatus = 'Powered down'
            if self.guestInfo['username'] not in 'None': #in case powerdown event hit before logoff
                self.guestInfo['lastUser'] = '' + self.guestInfo['username']
                self.guestInfo['username'] = 'None'
                self.guestInfo['lastLogout'] = time.time()
        elif guestMessage == guestMType.guestName:
            self.guestInfo['guestName'] = _filterXmlChars(unicode(body, 'utf8'))
        elif guestMessage == guestMType.guestOs:
            self.guestInfo['guestOs'] = _filterXmlChars(unicode(body, 'utf8'))
        elif guestMessage == guestMType.IPAddresses:
            guestIPs = body.strip().split()
            self.log.debug(str(guestIPs))
            self.guestInfo['guestIPs'] = _filterXmlChars(' '.join(guestIPs))
        elif guestMessage == guestMType.lastSessionMessage:
            lastSessionMessage = body
            self.log.debug(lastSessionMessage)
            if 'Logoff' in lastSessionMessage:
                self.guestInfo['lastUser'] = '' + self.guestInfo['username']
                self.guestInfo['username'] = 'None'
                self.guestInfo['lastLogout'] = time.time()
        elif guestMessage == guestMType.flushApps:
            if self._tempAppsList == self.guestInfo['appsList'] == []:
                self._firstAppsList = True
            else:
                self._firstAppsList = False
            self.guestInfo['appsList'] = self._tempAppsList
            self._tempAppsList = []
        elif guestMessage == guestMType.newApp:
            app = _filterXmlChars(unicode(body, 'utf8').strip())
            if app not in self._tempAppsList:
                self._tempAppsList.append(app)
            if self._firstAppsList:
                self.guestInfo['appsList'] = self._tempAppsList
        elif guestMessage == guestMType.userInfo:
            self.log.debug(body)
            currentUser = _filterXmlChars(unicode(body, 'utf8'))
            if (currentUser != self.guestInfo['username']) and not (currentUser=='Unknown' and self.guestInfo['username']=='None'):
                self.guestInfo['username'] = currentUser
                self.guestInfo['lastLogin'] = time.time()
            self.log.debug(repr(self.guestInfo['username']))
        elif guestMessage == guestMType.sessionLogon:
            self.guestInfo['session'] = "UserLoggedOn"
        elif guestMessage == guestMType.sessionLock:
            self.guestInfo['session'] = "Locked"
        elif guestMessage == guestMType.sessionUnlock:
            self.guestInfo['session'] = "Active"
        elif guestMessage == guestMType.sessionLogoff:
            self.guestInfo['session'] = "LoggedOff"
        elif guestMessage == guestMType.agentUninstalled:
            self.log.debug("RHEV agent was uninstalled.")
            self.guestInfo['appsList'] = []
        elif guestMessage == guestMType.sessionStartup:
            self.log.debug("Guest system is started or restarted.")
        elif guestMessage == guestMType.sessionShutdown:
            self.log.debug("Guest system shuts down.")
        else:
            self.log.error('Unknown message type %s', guestMessage)

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
            self._forward("lock screen")
        except:
            self.log.error(traceback.format_exc())

    def desktopLogin (self, domain, user, password):
        try:
            self.log.debug("desktopLogin called")
            if domain != '':
                username = user + '@' + domain
            else:
                username = user
            username = username.encode('utf-8')
            password = password.encode('utf-8')
            s = struct.pack('>6sI%ds%ds' % (len(username), len(password) + 1),
                        'login', len(username), username, password)
            self._forward(s)
        except:
            self.log.error(traceback.format_exc())

    def desktopLogoff (self, force):
        try:
            self.log.debug("desktopLogoff called")
            self._forward('log off')
        except:
            self.log.error(traceback.format_exc())

    def sendHcCmdToDesktop (self, cmd):
        try:
            self.log.debug("sendHcCmdToDesktop('%s')"%(cmd))
            self._forward(str(cmd))
        except:
            self.log.error(traceback.format_exc())

    READSIZE = 2**16
    def _readBuffer(self):
        while not self._stopped:
            try:
                s = self._sock.recv(self.READSIZE)
                if s:
                    self._buffer += s
                    self._agentTimestamp = time.time()
                    break
                time.sleep(1)
            except socket.timeout:
                # TODO move these specific bits out of here
                self.guestInfo['memUsage'] = 0
                if self.guestStatus not in ("Powered down", "RebootInProgress"):
                    self.log.log(logging.TRACE, "Guest connection timed out")
                    self.guestStatus = None

    def _readMessage(self):
        msg = None
        if len(self._buffer) >= headerLengthBytes:
            msglen = self._parseHeader(self._buffer[:headerLengthBytes])
            if len(self._buffer) >= headerLengthBytes + msglen:
                msg = self._buffer[headerLengthBytes:headerLengthBytes + msglen]
                self._buffer = self._buffer[headerLengthBytes + msglen:]
        return msg

    def _parseMessages(self):
        s = self._readMessage()
        while not self._stopped and s:
            self._parseBody(s)
            s = self._readMessage()

    def run(self):
        self._stopped = False
        try:
            if not self._connect():
                return
            self.sendHcCmdToDesktop('refresh')
            self._buffer = ''
            while not self._stopped:
                self._readBuffer()
                self._parseMessages()
        except:
            if not self._stopped:
                self.log.error("Unexpected exception: " + traceback.format_exc())
