#!/usr/bin/python
import os, sys, traceback
sys.path.append('/home/simong/dtv/vds/vdsm')
import time
from socket import socket, SOCK_STREAM, AF_UNIX
from define import myException, loggerConf
import logging
from logging import config as lconfig
from vm import guestIF
from guestIF import protocolMtype, guestMType, packMessage, wordSize



class guestServer:
    def __init__ (self, socketFile):
        self.socketFile = socketFile

    def waitConnection(self):
        socketFile = self.socketFile
        self.sock = socket(AF_UNIX, SOCK_STREAM)
        try:
            self.sock.bind(socketFile)
            self.sock.listen(1)
            self.channel, address = self.sock.accept() 
            print "Vdsm connected", self.channel, address
            return True
        except:
            print (traceback.format_exc())  
            self.connected = False
            return False


    def getMessage(self):
        message = self.channel.recv(1024)
        return message


def run():
    lconfig.fileConfig('ut/' + loggerConf)
    log = logging.getLogger('root')
    socektFile = '/tmp/temp.socket'
    try: 
        os.unlink(socektFile)
    except:
        pass
    server=guestServer(socektFile)
    toGuest=guestIF(socektFile, log)
    toGuest.start()
    server.waitConnection()
    print repr(server.getMessage())
    #send Powerup
    channel = 1
    mlen = wordSize * 4
    powerupMessage = [channel, protocolMtype.forward, mlen, guestMType.powerup]
    powerdownMessage = [channel, protocolMtype.forward, mlen, guestMType.powerdown]
    heartbeatMessage = [channel, protocolMtype.forward, mlen, guestMType.heartbeat]
    server.channel.send(packMessage(powerupMessage))
    for i in range(0, 10):
        server.channel.send(packMessage(heartbeatMessage))
        time.sleep(5)
    time.sleep(15)
    for i in range(0, 3):
        server.channel.send(packMessage(heartbeatMessage))
        time.sleep(5)
    server.channel.send(packMessage(powerdownMessage))
    toGuest.stop()
    toGuest.join()



if __name__ == '__main__':

    try:
        run()
    except myException, e:
        print e
        sys.exit(-1)
    except:
        print traceback.format_exc()
        sys.exit(-1)




