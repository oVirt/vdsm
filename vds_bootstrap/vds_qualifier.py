#!/usr/bin/python
#
# Copyright 2008 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#
# Script to check VDS compatibility.
# Input:
# Output: stdout as human readable format
#
# Steps to perform:
# 1. Check VT/SVM
# 2. OS name + version
# 3. Kernel version
# 4. Check missing RPMs
# 5. Check missing VDS packages
# 6. Check switch configuration
#

import sys, getopt
import logging, logging.config
import traceback
import random
import vds_bootstrap

rnum = random.randint(100,1000000).__repr__()

########################################################
##
##  Set of private functions.
##
########################################################
def VdsValidation(pack_url, remote_nfs, ver):
    """ --- Check VDS Compatibility.
    """
    ret = 0
    logging.debug("Entered VdsValidation(url = '%s', remote_nfs = '%s')"%(pack_url, remote_nfs))

    rc = vds_bootstrap.VirtExplorer(rnum, True).runTest()
    if rc:
        logging.error('VirtExplorer test fail')
        ret = -1

    rc = vds_bootstrap.OsExplorer(True).runTest()
    if rc:
        logging.error('OsExplorer test fail')
        ret = -1

    if not pack_url:
        rc = vds_bootstrap.PackagesExplorer(pack_url, ver, True, True).runTest()
    else:
        rc = vds_bootstrap.PackagesExplorer(pack_url, ver, False, False).runTest()
    if rc:
        logging.error('PackagesExplorer test fail')
        ret = -1

    return ret

def main():
    """ Usage: vds_qualifier.py
        [-m remote_nfs] - remote nfs path or local
        [-r repository] - url for yum update
        [-v revision]   - revision number
    """
    try:
        remote_nfs = ''
        url = ''
        rev = ''
        opts, args = getopt.getopt(sys.argv[1:], "m:r:v:")
        for o,v in opts:
            if o == "-m":
                remote_nfs = v
            if o == "-r":
                url = v
            if o == "-v":
                rev = v
    except:
        print main.__doc__
        return 0

    print '**** Start VDS Validation ****'
    logging.debug('**** Start VDS Validation ****')
    try:
        ret = VdsValidation(url, remote_nfs, rev)
    except:
        logging.error(traceback.format_exc())
        logging.error(main.__doc__)
        return 0
    else:
        if ret == -1:
            print '**** Vds Validation: FAIL'
            logging.debug('**** Vds Validation: FAIL')
        else:
            print '**** Vds Validation: OK'
            logging.debug('**** Vds Validation: OK')

if __name__ == "__main__":
    sys.exit(main())

