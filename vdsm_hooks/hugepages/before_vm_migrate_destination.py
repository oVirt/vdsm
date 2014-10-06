#!/usr/bin/python

import os
import sys
import traceback

import hooking

NUMBER_OF_HUGETPAGES = '/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages'


def addSysHugepages(pages):
    with open(NUMBER_OF_HUGETPAGES, 'r') as f:
        currPages = int(f.read())

    totalPages = pages + currPages
    # command: sysctl vm.nr_hugepages=256
    command = ['sysctl', 'vm.nr_hugepages=%d' % totalPages]
    retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('hugepages before_vm_migraton_destination: error in '
                         'command: %s, err = %s\n' % (' '.join(command), err))
        sys.exit(2)

    with open(NUMBER_OF_HUGETPAGES, 'r') as f:
        newCurrPages = int(f.read())

    return (newCurrPages - currPages)


def freeSysHugepages(pages):
    with open(NUMBER_OF_HUGETPAGES, 'r') as f:
        currPages = int(f.read())

    if pages > 0:
        # command: sysctl vm.nr_hugepages=0
        command = ['sysctl', 'vm.nr_hugepages=%d' % (currPages - pages)]
        retcode, out, err = hooking.execCmd(command, sudo=True, raw=True)
        if retcode != 0:
            sys.stderr.write('hugepages before_vm_migraton_destination: error '
                             'in command: %s, err = %s\n' %
                             (' '.join(command), err))
            sys.exit(2)


if 'hugepages' in os.environ:
    try:
        pages = int(os.environ.get('hugepages'))

        addSysHugepages(pages)
    except:
        sys.stderr.write('hugepages before_vm_migraton_destination: '
                         '[unexpected error]: %s\n' % traceback.format_exc())
        sys.exit(2)

        # Add system hugepages
        allocatedPages = addSysHugepages(pages)
        if allocatedPages != pages:
            freeSysHugepages(allocatedPages)
            sys.stderr.write('hugepages before_vm_migraton_destination: cannot'
                             ' allocate enough pages\n')
            sys.exit(2)
