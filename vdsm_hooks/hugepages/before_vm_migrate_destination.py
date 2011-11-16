#!/usr/bin/python

import os
import sys
import traceback
import utils

NUMBER_OF_HUGETPAGES = '/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages'

def addSysHugepages(pages):
    f = file(NUMBER_OF_HUGETPAGES, 'r')
    currPages = int(f.read())
    f.close()

    totalPages = pages + currPages
    # command: sysctl vm.nr_hugepages=256
    command = ['sysctl', 'vm.nr_hugepages=%d' % totalPages]
    retcode, out, err = utils.execCmd(command, sudo=True, raw=True)
    if retcode != 0:
        sys.stderr.write('hugepages before_vm_migraton_destination: error in command: %s, err = %s\n' % (' '.join(command), err))
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
        retcode, out, err = utils.execCmd(command, sudo=True, raw=True)
        if retcode != 0:
            sys.stderr.write('hugepages before_vm_migraton_destination: error in command: %s, err = %s\n' % (' '.join(command), err))
            sys.exit(2)


if os.environ.has_key('hugepages'):
    try:
        pages = int(os.environ.get('hugepages'))

        addSysHugepages(pages)
    except:
        sys.stderr.write('hugepages before_vm_migraton_destination: [unexpected error]: %s\n' % traceback.format_exc())
        sys.exit(2)



        # Add system hugepages
        allocatedPages = addSysHugepages(pages)
        if allocatedPages != pages:
            freeSysHugepages(allocatedPages)
            sys.stderr.write('hugepages before_vm_migraton_destination: cannot allocate enough pages\n')
            sys.exit(2)
