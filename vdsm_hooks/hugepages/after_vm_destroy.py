#!/usr/bin/python

import os
import sys
import traceback

NUMBER_OF_HUGETPAGES = '/sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages'

def removeSysHugepages(pages):
    f = file(NUMBER_OF_HUGETPAGES, 'r')
    currPages = int(f.read())
    f.close()

    totalPages = currPages - pages
    os.system('sudo sysctl vm.nr_hugepages=%d' % totalPages)

    sys.stderr.write('hugepages: removing %d huge pages\n' % pages)

if 'hugepages' in os.environ:
    try:
        pages = int(os.environ.get('hugepages'))

        removeSysHugepages(pages)
    except:
        sys.stderr.write('hugepages: [unexpected error]: %s\n' % traceback.format_exc())
        sys.exit(2)
