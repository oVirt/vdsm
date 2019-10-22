#
# Copyright 2009-2017 Red Hat, Inc.
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
import array
import os
import errno
import time
import threading
import struct
import logging

import uuid

from six.moves import queue

from vdsm.config import config
from vdsm.storage import misc
from vdsm.storage import task
from vdsm.storage.exception import InvalidParameterException
from vdsm.storage.threadPool import ThreadPool

from vdsm import constants
from vdsm.common import concurrent

__author__ = "ayalb"
__date__ = "$Mar 9, 2009 5:25:07 PM$"


CHECKSUM_BYTES = 4
MAILBOX_SIZE = 4096
PACKED_UUID_SIZE = 16
VOLUME_MAX_SIZE = 0xFFFFFFFF  # 64 bit unsigned max size
SIZE_CHARS = 16
MESSAGE_VERSION = b"1"
MESSAGE_SIZE = 64
CLEAN_MESSAGE = b"\1" * MESSAGE_SIZE
EXTEND_CODE = b"xtnd"
REPLY_OK = 1
EMPTYMAILBOX = MAILBOX_SIZE * b"\0"
SLOTS_PER_MAILBOX = int(MAILBOX_SIZE // MESSAGE_SIZE)
# Last message slot is reserved for metadata (checksum, extendable mailbox,
# etc)
MESSAGES_PER_MAILBOX = SLOTS_PER_MAILBOX - 1


def checksum(string, numBytes):
    bits = 8 * numBytes
    tmpArray = array.array('B')
    tmpArray.fromstring(string)
    csum = sum(tmpArray)
    return csum - (csum >> bits << bits)


_zeroCheck = checksum(EMPTYMAILBOX, CHECKSUM_BYTES)
# Assumes CHECKSUM_BYTES equals 4!!!
pZeroChecksum = struct.pack('<l', _zeroCheck)


def runTask(args):
    if type(args) == tuple:
        cmd = args[0]
        args = args[1:]
    else:
        cmd = args
        args = None
    ctask = task.Task(id=None, name=cmd)
    ctask.prepare(cmd, *args)


def _mboxExecCmd(*args, **kwargs):
    return misc.execCmd(*args, **kwargs)


class SPM_Extend_Message:

    log = logging.getLogger('storage.SPM.Messages.Extend')

    def __init__(self, volumeData, newSize, callbackFunction=None):
        if ('poolID' not in volumeData or
                'domainID' not in volumeData or
                'volumeID' not in volumeData):
            self.log.error('create extend msg failed for volume: %s, size:'
                           ' %d', '-'.join(volumeData.values()), newSize)
            raise InvalidParameterException('volumeData dictionary',
                                            volumeData)

        if (newSize < 0) or (newSize > VOLUME_MAX_SIZE):
            raise InvalidParameterException('volumeSize', newSize)

        misc.validateUUID(volumeData['domainID'], 'domainID')
        misc.validateUUID(volumeData['volumeID'], 'volumeID')

        self.pool = volumeData['poolID']
        self.volumeData = volumeData
        self.callback = callbackFunction

        # Message structure is rigid (order must be kept and is relied upon):
        # Version (1 byte), OpCode (4 bytes), Domain UUID (16 bytes), Volume
        # UUID (16 bytes), Requested size (16 bytes), Padding to 64 bytes (14
        # bytes)
        domain = misc.packUuid(volumeData['domainID'])
        volume = misc.packUuid(volumeData['volumeID'])
        size = b'%0*x' % (SIZE_CHARS, newSize)
        payload = MESSAGE_VERSION + EXTEND_CODE + domain + volume + size
        # Pad payload with zeros
        self.payload = payload.ljust(MESSAGE_SIZE, b"0")

        self.log.debug('new extend msg created: domain: %s, volume: %s',
                       volumeData['domainID'], volumeData['volumeID'])

    def __getitem__(self, index):
        return self.payload[index]

    def checkReply(self, reply):
        # Sanity check - Make sure reply is for current message
        sizeOffset = 5 + 2 * PACKED_UUID_SIZE
        if (self.payload[0:sizeOffset] != reply[0:sizeOffset]):
            self.log.error("SPM_Extend_Message: Reply message volume data "
                           "(domainID + volumeID) differs from request "
                           "message, reply : %s, orig: %s", reply,
                           self.payload)
            raise RuntimeError('Incorrect reply')
        # if self.payload[sizeOffset:sizeOffset + PACKED_UUID_SIZE] > \
        #        reply[sizeOffset:sizeOffset + PACKED_UUID_SIZE]):
        #    self.log.error("SPM_Extend_Message: New size is smaller than "
        #                   "requested size")
        #    raise RuntimeError('Request failed')
        return REPLY_OK

    @classmethod
    def processRequest(cls, pool, msgID, payload):
        cls.log.debug("processRequest, payload:" + repr(payload))
        sdOffset = 5
        volumeOffset = sdOffset + PACKED_UUID_SIZE
        sizeOffset = volumeOffset + PACKED_UUID_SIZE

        volume = {}
        volume['poolID'] = pool.spUUID
        volume['domainID'] = misc.unpackUuid(
            payload[sdOffset:sdOffset + PACKED_UUID_SIZE])
        volume['volumeID'] = misc.unpackUuid(
            payload[volumeOffset:volumeOffset + PACKED_UUID_SIZE])
        size = int(payload[sizeOffset:sizeOffset + SIZE_CHARS], 16)

        cls.log.info("processRequest: extending volume %s "
                     "in domain %s (pool %s) to size %d", volume['volumeID'],
                     volume['domainID'], volume['poolID'], size)

        msg = None
        try:
            try:
                pool.extendVolume(volume['domainID'], volume['volumeID'], size)
                msg = SPM_Extend_Message(volume, size)
            except:
                cls.log.error("processRequest: Exception caught while trying "
                              "to extend volume: %s in domain: %s",
                              volume['volumeID'], volume['domainID'],
                              exc_info=True)
                msg = SPM_Extend_Message(volume, 0)
        finally:
            pool.spmMailer.sendReply(msgID, msg)
            return {'status': {'code': 0, 'message': 'Done'}}


class HSM_Mailbox:

    log = logging.getLogger('storage.Mailbox.HSM')

    def __init__(self, hostID, poolID, inbox, outbox, monitorInterval=2):
        self._hostID = str(hostID)
        self._poolID = str(poolID)
        self._monitorInterval = monitorInterval
        self._queue = queue.Queue(-1)
        self._inbox = inbox
        if not os.path.exists(self._inbox):
            self.log.error("HSM_Mailbox create failed - inbox %s does not "
                           "exist" % repr(self._inbox))
            raise RuntimeError("HSM_Mailbox create failed - inbox %s does not "
                               "exist" % repr(self._inbox))
        self._outbox = outbox
        if not os.path.exists(self._outbox):
            self.log.error("HSM_Mailbox create failed - outbox %s does not "
                           "exist" % repr(self._outbox))
            raise RuntimeError("HSM_Mailbox create failed - outbox %s does "
                               "not exist" % repr(self._outbox))
        self._mailman = HSM_MailMonitor(self._inbox, self._outbox, hostID,
                                        self._queue, monitorInterval)
        self.log.debug('HSM_MailboxMonitor created for pool %s' % self._poolID)

    def sendExtendMsg(self, volumeData, newSize, callbackFunction=None):
        msg = SPM_Extend_Message(volumeData, newSize, callbackFunction)
        if str(msg.pool) != self._poolID:
            raise ValueError('PoolID does not correspond to Mailbox pool')
        self._queue.put(msg)

    def stop(self):
        if self._mailman:
            self._mailman.immStop()
            self._mailman.tp.joinAll()
        else:
            self.log.warning("HSM_MailboxMonitor - No mail monitor object "
                             "available to stop")

    def wait(self, timeout=None):
        return self._mailman.wait(timeout)


class HSM_MailMonitor(object):
    log = logging.getLogger('storage.MailBox.HsmMailMonitor')

    def __init__(self, inbox, outbox, hostID, queue, monitorInterval):
        # Save arguments
        tpSize = config.getint('irs', 'thread_pool_size') // 2
        waitTimeout = wait_timeout(monitorInterval)
        maxTasks = config.getint('irs', 'max_tasks')
        self.tp = ThreadPool("mailbox-hsm", tpSize, waitTimeout, maxTasks)
        self._stop = False
        self._queue = queue
        self._activeMessages = {}
        self._monitorInterval = monitorInterval
        self._hostID = int(hostID)
        self._used_slots_array = [0] * MESSAGES_PER_MAILBOX
        self._outgoingMail = EMPTYMAILBOX
        self._incomingMail = EMPTYMAILBOX
        # TODO: add support for multiple paths (multiple mailboxes)
        self._inCmd = [constants.EXT_DD,
                       'if=' + str(inbox),
                       'iflag=direct,fullblock',
                       'bs=' + str(MAILBOX_SIZE),
                       'count=1',
                       'skip=' + str(self._hostID)
                       ]
        self._outCmd = [constants.EXT_DD,
                        'of=' + str(outbox),
                        'iflag=fullblock',
                        'oflag=direct',
                        'conv=notrunc',
                        'bs=' + str(MAILBOX_SIZE),
                        'count=1',
                        'seek=' + str(self._hostID)
                        ]
        self._init = False
        self._initMailbox()  # Read initial mailbox state
        self._msgCounter = 0
        self._sendMail()  # Clear outgoing mailbox
        self._thread = concurrent.thread(self._run, name="mailbox-hsm",
                                         log=self.log)
        self._thread.start()

    def _initMailbox(self):
        # Sync initial incoming mail state with storage view
        (rc, out, err) = _mboxExecCmd(self._inCmd, raw=True)
        if rc == 0:
            self._incomingMail = out
            self._init = True
        else:
            self.log.warning("HSM_MailboxMonitor - Could not initialize "
                             "mailbox, will not accept requests until init "
                             "succeeds")

    def immStop(self):
        self._stop = True

    def wait(self, timeout=None):
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def _handleResponses(self, newMsgs):
        rc = False

        for i in range(0, MESSAGES_PER_MAILBOX):
            # Skip checking non used slots
            if self._used_slots_array[i] == 0:
                continue

            # Skip empty return messages (messages with version 0)
            start = i * MESSAGE_SIZE

            # First byte of message is message version.
            # Check return message version, if 0 then message is empty
            if newMsgs[start] in ['\0', '0']:
                continue

            for j in range(start, start + MESSAGE_SIZE):
                if newMsgs[j] != self._incomingMail[j]:
                    break

            # If search exhausted then message hasn't changed since last read
            # and can be skipped
            if j == (start + MESSAGE_SIZE - 1):
                continue

            #
            # We only get here if there is a novel reply so we can remove the
            # message from the active list and the outgoing mail and handle the
            # reply
            #
            rc = True

            newMsg = newMsgs[start:start + MESSAGE_SIZE]

            if newMsg == CLEAN_MESSAGE:
                del self._activeMessages[i]
                self._used_slots_array[i] = 0
                self._msgCounter -= 1
                self._outgoingMail = self._outgoingMail[0:start] + \
                    MESSAGE_SIZE * "\0" + self._outgoingMail[start +
                                                             MESSAGE_SIZE:]
                continue

            msg = self._activeMessages[i]
            self._activeMessages[i] = CLEAN_MESSAGE
            self._outgoingMail = self._outgoingMail[0:start] + \
                CLEAN_MESSAGE + self._outgoingMail[start + MESSAGE_SIZE:]

            try:
                self.log.debug("HSM_MailboxMonitor(%s/%s) - Checking reply: "
                               "%s", self._msgCounter, MESSAGES_PER_MAILBOX,
                               repr(newMsg))
                msg.checkReply(newMsg)
                if msg.callback:
                    try:
                        id = str(uuid.uuid4())
                        if not self.tp.queueTask(id, runTask, (msg.callback,
                                                 msg.volumeData)):
                            raise Exception()
                    except:
                        self.log.error("HSM_MailMonitor: exception caught "
                                       "while running msg callback, for "
                                       "message: %s, callback function: %s",
                                       repr(msg.payload), msg.callback,
                                       exc_info=True)
            except RuntimeError as e:
                self.log.error("HSM_MailMonitor: exception: %s caught while "
                               "checking reply for message: %s, reply: %s",
                               str(e), repr(msg.payload), repr(newMsg))
            except:
                self.log.error("HSM_MailMonitor: exception caught while "
                               "checking reply from SPM, request was: %s "
                               "reply: %s", repr(msg.payload), repr(newMsg),
                               exc_info=True)
        # Finished processing incoming mail, now save mail to compare against
        # next batch
        self._incomingMail = newMsgs
        return rc

    def _checkForMail(self):
        # self.log.debug("HSM_MailMonitor - checking for mail")
        # self.log.debug("Running command: " + str(self._inCmd))
        (rc, in_mail, err) = misc.execCmd(self._inCmd, raw=True)
        if rc:
            raise RuntimeError("_handleResponses.Could not read mailbox - rc "
                               "%s" % rc)
        if (len(in_mail) != MAILBOX_SIZE):
            raise RuntimeError("_handleResponses.Could not read mailbox - len "
                               "%s != %s" % (len(in_mail), MAILBOX_SIZE))
        # self.log.debug("Parsing inbox content: %s", in_mail)
        return self._handleResponses(in_mail)

    def _sendMail(self):
        self.log.info("HSM_MailMonitor sending mail to SPM - " +
                      str(self._outCmd))
        chk = checksum(
            self._outgoingMail[0:MAILBOX_SIZE - CHECKSUM_BYTES],
            CHECKSUM_BYTES)
        pChk = struct.pack('<l', chk)  # Assumes CHECKSUM_BYTES equals 4!!!
        self._outgoingMail = \
            self._outgoingMail[0:MAILBOX_SIZE - CHECKSUM_BYTES] + pChk
        _mboxExecCmd(self._outCmd, data=self._outgoingMail)

    def _handleMessage(self, message):
        # TODO: add support for multiple mailboxes
        freeSlot = False
        for i in range(0, MESSAGES_PER_MAILBOX):
            if self._used_slots_array[i] == 0:
                if not freeSlot:
                    freeSlot = i
                continue
            duplicate = True
            for j in range(0, MESSAGE_SIZE):
                if message[j] != self._activeMessages[i][j]:
                    duplicate = False
                    break
            if duplicate:
                self.log.debug("HSM_MailMonitor - ignoring duplicate message "
                               "%s" % (repr(message)))
                return
        if not freeSlot:
            raise RuntimeError("HSM_MailMonitor - Active messages list full, "
                               "cannot add new message")

        self._msgCounter += 1
        self._used_slots_array[freeSlot] = 1
        self._activeMessages[freeSlot] = message
        start = freeSlot * MESSAGE_SIZE
        end = start + MESSAGE_SIZE
        self._outgoingMail = self._outgoingMail[0:start] + message.payload + \
            self._outgoingMail[end:]
        self.log.debug("HSM_MailMonitor - start: %s, end: %s, len: %s, "
                       "message(%s/%s): %s" %
                       (start, end, len(self._outgoingMail), self._msgCounter,
                        MESSAGES_PER_MAILBOX,
                        repr(self._outgoingMail[start:end])))

    def _run(self):
        try:
            failures = 0

            # Do not start processing requests before incoming mailbox is
            # initialized
            while not self._init and not self._stop:
                try:
                    time.sleep(2)
                    self._initMailbox()  # Read initial mailbox state
                except:
                    pass

            while not self._stop:
                try:
                    message = None
                    sendMail = False
                    # If no message is pending, block_wait until a new message
                    # or stop command arrives
                    while not self._stop and not message and \
                            not self._activeMessages:
                        try:
                            # self.log.debug("No requests in queue, going to "
                            #               "sleep until new requests arrive")
                            # Check if a new message is waiting to be sent
                            message = self._queue.get(
                                block=True, timeout=self._monitorInterval)
                            self._handleMessage(message)
                            message = None
                            sendMail = True
                        except queue.Empty:
                            pass

                    if self._stop:
                        break

                    # If pending messages available, check if there are new
                    # messages waiting in queue as well
                    empty = False
                    while (not empty) and \
                            (len(self._activeMessages) < MESSAGES_PER_MAILBOX):
                        # TODO: Remove single mailbox limitation
                        try:
                            message = self._queue.get(block=False)
                            self._handleMessage(message)
                            message = None
                            sendMail = True
                        except queue.Empty:
                            empty = True

                    try:
                        sendMail |= self._checkForMail()
                        failures = 0
                    except:
                        self.log.error("HSM_MailboxMonitor - Exception caught "
                                       "while checking for mail",
                                       exc_info=True)
                        failures += 1

                    if sendMail:
                        self._sendMail()

                    # If there are active messages waiting for SPM reply, wait
                    # a few seconds before performing another IO op
                    if self._activeMessages and not self._stop:
                        # If recurring failures then sleep for one minute
                        # before retrying
                        if (failures > 9):
                            time.sleep(60)
                        else:
                            time.sleep(self._monitorInterval)

                except:
                    self.log.error("HSM_MailboxMonitor - Incoming mail"
                                   "monitoring thread caught exception; "
                                   "will try to recover", exc_info=True)
        finally:
            self.log.info("HSM_MailboxMonitor - Incoming mail monitoring "
                          "thread stopped, clearing outgoing mail")
            self._outgoingMail = EMPTYMAILBOX
            self._sendMail()  # Clear outgoing mailbox


class SPM_MailMonitor:

    log = logging.getLogger('storage.MailBox.SpmMailMonitor')

    def registerMessageType(self, messageType, callback):
        self._messageTypes[messageType] = callback

    def unregisterMessageType(self, messageType):
        del self._messageTypes[messageType]

    def __init__(self, poolID, maxHostID, inbox, outbox, monitorInterval=2):
        """
        Note: inbox paramerter here should point to the HSM's outbox
        mailbox file, and vice versa.
        """
        self._messageTypes = {}
        # Save arguments
        self._stop = False
        self._stopped = False
        self._poolID = poolID
        tpSize = config.getint('irs', 'thread_pool_size') // 2
        waitTimeout = wait_timeout(monitorInterval)
        maxTasks = config.getint('irs', 'max_tasks')
        self.tp = ThreadPool("mailbox-spm", tpSize, waitTimeout, maxTasks)
        self._inbox = inbox
        if not os.path.exists(self._inbox):
            self.log.error("SPM_MailMonitor create failed - inbox %s does not "
                           "exist" % repr(self._inbox))
            raise RuntimeError("SPM_MailMonitor create failed - inbox %s does "
                               "not exist" % repr(self._inbox))
        self._outbox = outbox
        if not os.path.exists(self._outbox):
            self.log.error("SPM_MailMonitor create failed - outbox %s does "
                           "not exist" % repr(self._outbox))
            raise RuntimeError("SPM_MailMonitor create failed - outbox %s "
                               "does not exist" % repr(self._outbox))
        self._numHosts = int(maxHostID)
        self._outMailLen = MAILBOX_SIZE * self._numHosts
        self._monitorInterval = monitorInterval
        # TODO: add support for multiple paths (multiple mailboxes)
        self._outgoingMail = self._outMailLen * b"\0"
        self._incomingMail = self._outgoingMail
        self._inCmd = ['dd',
                       'if=' + str(self._inbox),
                       'iflag=direct,fullblock',
                       'count=1'
                       ]
        self._outCmd = ['dd',
                        'of=' + str(self._outbox),
                        'oflag=direct',
                        'iflag=fullblock',
                        'conv=notrunc',
                        'count=1'
                        ]
        self._outLock = threading.Lock()
        self._inLock = threading.Lock()
        # Clear outgoing mail
        self.log.debug("SPM_MailMonitor - clearing outgoing mail, command is: "
                       "%s", self._outCmd)
        cmd = self._outCmd + ['bs=' + str(self._outMailLen)]
        (rc, out, err) = _mboxExecCmd(cmd, data=self._outgoingMail)
        if rc:
            self.log.warning("SPM_MailMonitor couldn't clear outgoing mail, "
                             "dd failed")

        self._thread = concurrent.thread(
            self._run, name="mailbox-spm", log=self.log)
        self.log.debug('SPM_MailMonitor created for pool %s' % self._poolID)

    def start(self):
        self._thread.start()

    def wait(self, timeout=None):
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def stop(self):
        self._stop = True

    def isStopped(self):
        return self._stopped

    @classmethod
    def validateMailbox(self, mailbox, mailboxIndex):
        """
        Return True if mailbox has a valid checksum, and is not an empty
        mailbox, False otherwise.
        """
        assert len(mailbox) == MAILBOX_SIZE
        data = mailbox[:-CHECKSUM_BYTES]
        csum = mailbox[-CHECKSUM_BYTES:]
        n = checksum(data, CHECKSUM_BYTES)
        expected = struct.pack('<l', n)  # Assumes CHECKSUM_BYTES equals 4!!!
        if csum != expected:
            self.log.error(
                "mailbox %s checksum failed, not clearing mailbox, clearing "
                "new mail (data=%r, checksum=%r, expected=%r)",
                mailboxIndex, data, checksum, expected)
            return False
        elif expected == pZeroChecksum:
            return False  # Ignore messages of empty mailbox
        return True

    def _handleRequests(self, newMail):

        send = False

        # run through all messages and check if new messages have arrived
        # (since last read)
        for host in range(0, self._numHosts):
            # Check mailbox checksum
            mailboxStart = host * MAILBOX_SIZE

            isMailboxValidated = False

            for i in range(0, MESSAGES_PER_MAILBOX):

                msgId = host * SLOTS_PER_MAILBOX + i
                msgStart = msgId * MESSAGE_SIZE

                # First byte of message is message version.  Check message
                # version, if 0 then message is empty and can be skipped
                if newMail[msgStart] in ['\0', '0']:
                    continue

                # Most mailboxes are probably empty so it costs less to check
                # that all messages start with 0 than to validate the mailbox,
                # therefor this is done after we find a non empty message in
                # mailbox
                if not isMailboxValidated:
                    if not self.validateMailbox(
                            newMail[mailboxStart:mailboxStart + MAILBOX_SIZE],
                            host):
                        # Cleaning invalid mbx in newMail
                        newMail = newMail[:mailboxStart] + EMPTYMAILBOX + \
                            newMail[mailboxStart + MAILBOX_SIZE:]
                        break
                    self.log.debug("SPM_MailMonitor: Mailbox %s validated, "
                                   "checking mail", host)
                    isMailboxValidated = True

                newMsg = newMail[msgStart:msgStart + MESSAGE_SIZE]
                msgOffset = msgId * MESSAGE_SIZE
                if newMsg == CLEAN_MESSAGE:
                    # Should probably put a setter on outgoingMail which would
                    # take the lock
                    with self._outLock:
                        self._outgoingMail = \
                            self._outgoingMail[0:msgOffset] + CLEAN_MESSAGE + \
                            self._outgoingMail[msgOffset + MESSAGE_SIZE:
                                               self._outMailLen]
                    send = True
                    continue

                # Message isn't empty, check if its new
                isMessageNew = False
                for j in range(msgStart, msgStart + MESSAGE_SIZE):
                    if newMail[j] != self._incomingMail[j]:
                        isMessageNew = True
                        break

                # If search exhausted, i.e. message hasn't changed since last
                # read, it can be skipped
                if not isMessageNew:
                    continue

                # We only get here if there is a novel request
                try:
                    msgType = newMail[msgStart + 1:msgStart + 5]
                    if msgType in self._messageTypes:
                        # Use message class to process request according to
                        # message specific logic
                        id = str(uuid.uuid4())
                        self.log.debug("SPM_MailMonitor: processing request: "
                                       "%s" % repr(newMail[
                                           msgStart:msgStart + MESSAGE_SIZE]))
                        res = self.tp.queueTask(
                            id, runTask, (self._messageTypes[msgType], msgId,
                                          newMail[msgStart:
                                                  msgStart + MESSAGE_SIZE])
                        )
                        if not res:
                            raise Exception()
                    else:
                        self.log.error("SPM_MailMonitor: unknown message type "
                                       "encountered: %s", msgType)
                except RuntimeError as e:
                    self.log.error("SPM_MailMonitor: exception: %s caught "
                                   "while handling message: %s", str(e),
                                   newMail[msgStart:msgStart + MESSAGE_SIZE])
                except:
                    self.log.error("SPM_MailMonitor: exception caught while "
                                   "handling message: %s",
                                   newMail[msgStart:msgStart + MESSAGE_SIZE],
                                   exc_info=True)

        self._incomingMail = newMail
        return send

    def _checkForMail(self):
        # Lock is acquired in order to make sure that
        # incomingMail is not changed during checkForMail
        with self._inLock:
            # self.log.debug("SPM_MailMonitor -_checking for mail")
            cmd = self._inCmd + ['bs=' + str(self._outMailLen)]
            # self.log.debug("SPM_MailMonitor - reading incoming mail, "
            #               "command: " + str(cmd))
            (rc, in_mail, err) = misc.execCmd(cmd, raw=True)
            if rc:
                raise IOError(errno.EIO, "_handleRequests._checkForMail - "
                              "Could not read mailbox: %s" % self._inbox)

            if (len(in_mail) != (self._outMailLen)):
                self.log.error('SPM_MailMonitor: _checkForMail - dd succeeded '
                               'but read %d bytes instead of %d, cannot check '
                               'mail.  Read mail contains: %s', len(in_mail),
                               self._outMailLen, repr(in_mail[:80]))
                raise RuntimeError("_handleRequests._checkForMail - Could not "
                                   "read mailbox")
            # self.log.debug("Parsing inbox content: %s", in_mail)
            if self._handleRequests(in_mail):
                with self._outLock:
                    cmd = self._outCmd + ['bs=' + str(self._outMailLen)]
                    (rc, out, err) = _mboxExecCmd(cmd,
                                                  data=self._outgoingMail)
                    if rc:
                        self.log.warning("SPM_MailMonitor couldn't write "
                                         "outgoing mail, dd failed")

    def sendReply(self, msgID, msg):
        # Lock is acquired in order to make sure that
        # outgoingMail is not changed while used
        with self._outLock:
            msgOffset = msgID * MESSAGE_SIZE
            self._outgoingMail = \
                self._outgoingMail[0:msgOffset] + msg.payload + \
                self._outgoingMail[msgOffset + MESSAGE_SIZE:self._outMailLen]
            mailboxOffset = (msgID // SLOTS_PER_MAILBOX) * MAILBOX_SIZE
            mailbox = self._outgoingMail[mailboxOffset:
                                         mailboxOffset + MAILBOX_SIZE]
            cmd = self._outCmd + ['bs=' + str(MAILBOX_SIZE),
                                  'seek=' + str(mailboxOffset // MAILBOX_SIZE)]
            # self.log.debug("Running command: %s, for message id: %s",
            #               str(cmd), str(msgID))
            (rc, out, err) = _mboxExecCmd(cmd, data=mailbox)
            if rc:
                self.log.error("SPM_MailMonitor: sendReply - couldn't send "
                               "reply, dd failed")

    def _run(self):
        try:
            while not self._stop:
                try:
                    self._checkForMail()
                except:
                    self.log.error("Error checking for mail", exc_info=True)
                time.sleep(self._monitorInterval)
        finally:
            self._stopped = True
            self.tp.joinAll()
            self.log.info("SPM_MailMonitor - Incoming mail monitoring thread "
                          "stopped")


def wait_timeout(monitor_interval):
    """
    Designed to return 3 seconds wait timeout for monitor interval of 2
    seconds, keeping the behaivor in runtime the same as it was in the last 8
    years, while allowing shorter times for testing.
    """
    return monitor_interval * 3 / 2
