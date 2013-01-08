#!/usr/bin/python

import os
import sys
import traceback

import hooking

'''
hugepages vdsm hook
===================
hook is getting hugepages=512 and will preserve 512
huge pages

hook is doing the following:
add pages: sysctl vm.nr_hugepages=516
add the following xml in domain\devices:
    <memoryBacking>
        <hugepages/>
    </memoryBacking>

NOTE:
hugepages must! be mounted prior to libvirt start up,
ie:
# mount -t hugetlbfs hugetlbfs /dev/hugepages
# initctl restart libvirtd

Syntax:
hugepages=512
'''

HUGEPAGES_MOUNT_PATH = '/dev/hugepages'
QEMU_HUGEPAGES_MOUNT_PATH = HUGEPAGES_MOUNT_PATH + '/libvirt/qemu'
NUMBER_OF_HUGETPAGES = '/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages'


def addSysHugepages(pages):
    f = file(NUMBER_OF_HUGETPAGES, 'r')
    currPages = int(f.read())
    f.close()

    totalPages = pages + currPages
    # command: sysctl vm.nr_hugepages=256
    command = ['sysctl', 'vm.nr_hugepages=%d' % totalPages]
    retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('hugepages: error in command: %s, err = %s\n' %
                         (' '.join(command), err))
        sys.exit(2)

    f = file(NUMBER_OF_HUGETPAGES, 'r')
    newCurrPages = int(f.read())
    f.close()

    return (newCurrPages - currPages)


def freeSysHugepages(pages):
    f = file(NUMBER_OF_HUGETPAGES, 'r')
    currPages = int(f.read())
    f.close()

    if pages > 0:
        # command: sysctl vm.nr_hugepages=0
        command = ['sysctl', 'vm.nr_hugepages=%d' % (currPages - pages)]
        retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
        if retcode != 0:
            sys.stderr.write('hugepages: error in command: %s, err = %s\n' %
                             (' '.join(command), err))
            sys.exit(2)


if 'hugepages' in os.environ:
    try:
        domxml = hooking.read_domxml()

        pages = int(os.environ.get('hugepages'))

        domain = domxml.getElementsByTagName('domain')[0]

        if len(domain.getElementsByTagName('memoryBacking')):
            sys.stderr.write('hugepages: VM already have hugepages\n')
            sys.exit(0)

        # Add system hugepages
        allocatedPages = addSysHugepages(pages)
        if allocatedPages != pages:
            freeSysHugepages(allocatedPages)
            sys.stderr.write('hugepages: cannot allocate enough pages\n')
            sys.exit(2)

        # Add hugepages to libvirt xml
        memoryBacking = domxml.createElement('memoryBacking')
        hugepages = domxml.createElement('hugepages')
        memoryBacking.appendChild(hugepages)
        domain.appendChild(memoryBacking)

        sys.stderr.write('hugepages: adding hugepages tag\n')

        hooking.write_domxml(domxml)
    except:
        sys.stderr.write('hugepages: [unexpected error]: %s\n' %
                         traceback.format_exc())
        sys.exit(2)
