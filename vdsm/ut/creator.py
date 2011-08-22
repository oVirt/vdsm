#!/usr/bin/python
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

import commands
import sys

class creator:
    def __init__ (self, host, irs, parentImage, memSize):
        self.vms = {}
        self.host = host
        self.irs = irs
        self.parentImage = parentImage
        self.memSize = memSize
        self.vmPrefix = 'qum'
        self.macAddrPrefix = '00:1A:4A:16:75:'
        self.macAddrFirstSuffix = 80

    def createSnapshot (self, i):
        command = './irsClient.py ' + self.irs + ' snapshot ' + self.parentImage
        status, output = self.run(command)
        if (status == 0):
            self.vms[i] = output
            command = './irsClient.py ' + self.irs + ' setdescr '\
                + output + ' creator_temp' + str(i)
            self.run(command)

    def deleteSnapshot (self, i):
        command = './irsClient.py ' + self.irs + ' del ' + self.vms[i]
        status, output = self.run(command)
        if (status == 0):
            del self.vms[i]

    def createVm (self, i):
        command = './vdsClient.py ' + self.host\
            + ' create /dev/null vmId=' + self.vmPrefix + str(i)\
            + ' macAddr=' + self.macAddrPrefix + hex(self.macAddrFirstSuffix+i)[2:]\
            + ' memSize=' + str(self.memSize) + ' vmType=kvm display=vnc hda='\
            + '`./irsClient.py ' + self.irs + ' getpath ' + self.vms[i] + '`'
        self.run(command)

    def destroyVm (self, i):
        command = './vdsClient.py ' + self.host + ' destroy ' + self.vmPrefix + str(i)
        self.run(command)

    def destroyAllVms (self):
        vmKeys = self.vms.keys()
        for i in vmKeys:
            self.destroyVm(i)
            self.deleteSnapshot(i)

    def listBalloons (self):
        command = './vdsClient.py ' + self.host + ' list | grep balloon'
        self.run(command)

    def run (self, command):
        print command
        status, output = commands.getstatusoutput(command)
        print '==>', status, output
        print ''
        return status, output

if __name__ == '__main__':
    args = sys.argv[1:]
    if len(args) < 4:
        print 'Usage: creator.py host irs parentImage memSize'
        sys.exit(-1)

    vmc = creator(args[0], args[1], args[2], args[3])
    opt = ''
    while not (opt in ['x', 'X']):
        n = len(vmc.vms)
        print '=========='
        print 'vms:', str(n), vmc.vms
#        print commands.getoutput('head -n 4 /proc/meminfo')
        print '=========='
        opt = raw_input('[C]reate snapshot & vm, [D]estroy all vms & delete all snapshots, [B]alloons or e[X]it...')
        if opt in ['c','C']:
            vmc.createSnapshot(n)
            vmc.createVm(n)
        elif opt in ['d','D']:
            vmc.destroyAllVms()
        elif opt in ['b','B']:
            vmc.listBalloons()

