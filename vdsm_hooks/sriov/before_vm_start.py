#!/usr/bin/python

import os
import sys
import grp
import pwd
import traceback
from xml.dom import minidom

import hooking
from vdsm import libvirtconnection

SYS_NIC_PATH = '/sys/class/net/%s'
VDSM_VAR_HOOKS_DIR = '/var/run/vdsm/hooks'
SRIOV_CACHE_FILENAME = 'sriov.cache'

'''
sriov vdsm hook
===============
hook is getting VF via its os nic names, i.e. sriov=eth5
and get its pci address, detach it from the os, create xml
representation of the device for libvirt domain and adding
it to the guest xml.
'''


def getDeviceDetails(addr):
    ''' investigate device by its address and return
    [bus, slot, function] list
    '''

    connection = libvirtconnection.get(None)
    nodeDevice = connection.nodeDeviceLookupByName(addr)

    devXml = minidom.parseString(nodeDevice.XMLDesc(0))

    bus = hex(int(devXml.getElementsByTagName('bus')[0].firstChild.nodeValue))
    slot = hex(int(
               devXml.getElementsByTagName('slot')[0]
                     .firstChild.nodeValue))
    function = hex(int(
                   devXml.getElementsByTagName('function')[0]
                         .firstChild.nodeValue))

    sys.stderr.write('sriov: bus=%s slot=%s function=%s\n' %
                     (bus, slot, function))

    return (bus, slot, function)


def detachDevice(addr):
    ''' detach device from host, enable attaching it to VM '''

    connection = libvirtconnection.get(None)
    nodeDevice = connection.nodeDeviceLookupByName(addr)
    if nodeDevice != None:
        sys.stderr.write('sriov: detaching pci device: %s\n' % addr)
        nodeDevice.dettach()
    else:
        sys.stderr.write('sriov: cannot detach device: %s\n' % addr)


def createSriovElement(domxml, bus, slot, function):
    '''
    create host device element for libvirt domain xml:

    <hostdev mode='subsystem' type='pci'>
        <source>
            <address bus='0x1a' slot='0x10' function='0x06'/>
        </source>
    </hostdev>
    '''

    hostdev = domxml.createElement('hostdev')
    hostdev.setAttribute('mode', 'subsystem')
    hostdev.setAttribute('type', 'pci')

    source = domxml.createElement('source')
    hostdev.appendChild(source)

    address = domxml.createElement('address')
    address.setAttribute('bus', bus)
    address.setAttribute('slot', slot)
    address.setAttribute('function', function)

    source.appendChild(address)
    return hostdev


def deviceExists(devName):
    return os.path.exists(SYS_NIC_PATH % devName)


def getPciAddress(devPath):
    '''
    return pci address in format that libvirt expect:
    linux pci address 0000:1a:10.6
    libvirt expected 0000_1a_10_6
    '''
    p = os.path.split(devPath)
    tokens = p[1].split(':')
    return 'pci_%s_%s_%s' % (tokens[0], tokens[1], tokens[2].replace('.', '_'))


def writeSriovCache(name, addr, devpath):
    if not os.path.exists(VDSM_VAR_HOOKS_DIR):
        os.makedirs(VDSM_VAR_HOOKS_DIR)

    f = open(VDSM_VAR_HOOKS_DIR + '/' + SRIOV_CACHE_FILENAME, 'a')
    f.write(name + '=' + addr + '=' + devpath + '\n')
    f.close()


def chown(devpath):

    group = grp.getgrnam('qemu')
    gid = group.gr_gid
    user = pwd.getpwnam('qemu')
    uid = user.pw_uid

    for f in os.listdir(devpath):
        if f.startswith('resource') or f == 'rom' or f == 'reset':
            dev = os.path.join(devpath, f)

            # we don't use os.chown because we need sudo
            owner = str(uid) + ':' + str(gid)
            command = ['/bin/chown', owner, dev]
            retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
            if retcode != 0:
                sys.stderr.write('sriov: error chown %s to %s, err = %s\n' %
                                 (dev, owner, err))
                sys.exit(2)

if 'sriov' in os.environ:
    try:
        nics = os.environ['sriov']

        domxml = hooking.read_domxml()
        devices = domxml.getElementsByTagName('devices')[0]

        for nic in nics.split(','):
            if deviceExists(nic):
                sys.stderr.write('sriov: adding VF %s\n' % nic)

                devpath = os.path.realpath(SYS_NIC_PATH % nic + '/device')
                addr = getPciAddress(devpath)
                detachDevice(addr)
                bus, slot, function = getDeviceDetails(addr)

                hostdev = createSriovElement(domxml, bus, slot, function)

                sys.stderr.write('sriov: VF %s xml: %s\n' %
                                 (nic, hostdev.toxml()))
                devices.appendChild(hostdev)
                chown(devpath)
                writeSriovCache(nic, addr, devpath)
            else:
                sys.stderr.write('sriov: cannot find nic "%s", aborting\n' %
                                 nic)
                sys.exit(2)

        hooking.write_domxml(domxml)
    except:
        sys.stderr.write('sriov: [unexpected error]: %s\n' %
                         traceback.format_exc())
        sys.exit(2)
