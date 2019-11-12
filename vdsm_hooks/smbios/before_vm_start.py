#!/usr/bin/python3

from __future__ import absolute_import

import os
import sys
import ast
import hooking
import traceback

'''
smbios vdsm hook
================
adding changing entries to smbios domain entry:


syntax:
smbios={'serial': '1234'}^{'vendor': 'oVirt'}
'''

bios_entries = ["vendor", "version", "date", "release"]


def addSystemEntry(domxml, sysinfo, entry):
    systems = sysinfo.getElementsByTagName('system')
    if systems.length == 0:
        system = domxml.createElement('system')
        sysinfo.appendChild(system)
    else:
        system = systems[0]

    updated = False
    entries = system.getElementsByTagName('entry')
    for e in entries:
        updated = False
        # if exists we update else create new
        if e.hasAttribute('name') and e.attributes['name'].value in entry:
            e.childNodes[0].nodeValue = entry[e.attributes['name'].value]
            updated = True
            break

    if not updated:
        name = next(entry.iterkeys())
        e = domxml.createElement('entry')
        e.setAttribute('name', name)
        txt = domxml.createTextNode(entry[name])
        e.appendChild(txt)
        system.appendChild(e)


def addBiosEntry(domxml, sysinfo, entry):
    bioses = sysinfo.getElementsByTagName('bios')
    if bioses.length == 0:
        bios = domxml.createElement('bios')
        sysinfo.appendChild(bios)
    else:
        bios = bioses[0]

    updated = False
    entries = bios.getElementsByTagName('entry')
    for e in entries:
        updated = False
        # if exists we update else create new
        if e.hasAttribute('name') and e.attributes['name'].value in entry:
            e.childNodes[0].nodeValue = entry[e.attributes['name'].value]
            updated = True
            break

    if not updated:
        name = next(entry.iterkeys())
        e = domxml.createElement('entry')
        e.setAttribute('name', name)
        txt = domxml.createTextNode(entry[name])
        e.appendChild(txt)
        bios.appendChild(e)

if 'smbios' in os.environ:
    try:
        data = os.environ['smbios']

        domxml = hooking.read_domxml()
        sysinfos = domxml.getElementsByTagName('sysinfo')
        domain = domxml.getElementsByTagName('domain')[0]

        sysinfo = None
        for s in sysinfos:
            if s.attributes['type'].value == 'smbios':
                sysinfo = s
                break

        if sysinfo is None:
            sysinfo = domxml.createElement('sysinfo')
            sysinfo.setAttribute('type', 'smbios')
            domain.appendChild(sysinfo)

        for d in data.split('^'):
            # convert string to dictionary
            entry = ast.literal_eval(d)
            name = next(entry.iterkeys())
            if name in bios_entries:
                addBiosEntry(domxml, sysinfo, entry)
            else:
                addSystemEntry(domxml, sysinfo, entry)

        hooking.write_domxml(domxml)

    except:
        sys.stderr.write('smbios: [unexpected error]: %s\n' %
                         traceback.format_exc())
        sys.exit(2)
