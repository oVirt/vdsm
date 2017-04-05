#!/usr/bin/python2

import os
import sys
import traceback

import hooking

from vdsm import hugepages

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


if 'hugepages' in os.environ:
    try:
        domxml = hooking.read_domxml()

        pages = int(os.environ.get('hugepages'))

        domain = domxml.getElementsByTagName('domain')[0]

        if len(domain.getElementsByTagName('memoryBacking')):
            sys.stderr.write('hugepages: VM already have hugepages\n')
            sys.exit(0)

        # Add system hugepages
        allocatedPages = hugepages.alloc(pages)

        # Add hugepages to libvirt xml
        memoryBacking = domxml.createElement('memoryBacking')
        hugepages = domxml.createElement('hugepages')
        memoryBacking.appendChild(hugepages)
        domain.appendChild(memoryBacking)

        sys.stderr.write('hugepages: adding hugepages tag\n')

        hooking.write_domxml(domxml)
    except Exception:
        sys.stderr.write('hugepages: [unexpected error]: %s\n' %
                         traceback.format_exc())
        sys.exit(2)
