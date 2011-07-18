#!/usr/bin/python
# Script to complete vdsm installation.
# Input: certificate subject + current random ID.
# Output: stdout as XML format
#
# Steps to perform: Initiate Certificate Initalization
#   a. Clean previous certificates / keys.
#   b. Generate certificate and sign request
#

import sys
import getopt
#import pwd
#import errno
#from stat import *
from time import strftime
import logging
import logging.config
import traceback
import random
import ConfigParser
import deployUtil

rnum = random.randint(100,1000000).__repr__()
log_filename = '/var/log/vdsm-reg/vds_bootstrap_gen.'+strftime("%Y%m%d_%H%M%S")+'.log'

logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(levelname)-8s %(message)s',
                    datefmt='%a, %d %b %Y %H:%M:%S',
                    filename=log_filename,
                    filemode='w')

class CSR:
    """
        Makes sure that vdsmd has its Certificate in place
    """
    def __init__(self, sub='', num='', orgName='Red Hat, Inc.'):
        self.orgName = orgName
        self.subject = sub
        self.random_num = num

        # NOTE: /etc/vdsm/vdsm.conf must exist (Should exist after rpms are isntalled)
        config = ConfigParser.ConfigParser()
        config.read('/etc/vdsm/vdsm.conf')
        try:
            tsDir = config.get('vars', 'trust_store_path')
        except:
            tsDir = '/etc/pki/vdsm'

        self.tsDir = tsDir
        self.VDSMKEY = tsDir + "/keys/vdsmkey.pem"
        self.VDSMCERT = tsDir + "/certs/vdsm-" + sub + "-cert.pem"
        self.DHKEY = tsDir + '/keys/dh.pem'

    def xmlOutput(self):
        print "<BSTRAP component='Encryption setup' status='OK'/>"
        logging.debug("<BSTRAP component='Encryption setup' status='OK'/>")
        sys.stdout.flush()

    def runTest(self):
        deployUtil.pkiCleanup(self.VDSMKEY, self.VDSMCERT)
        deployUtil.createCSR(self.orgName, self.subject, self.random_num, self.tsDir, self.VDSMKEY, self.DHKEY)
        self.xmlOutput()

def main():
    """Usage: vdsm-gen-cert [-O organizationName] <subject> <random_num>"""
    try:
        orgName = 'Red Hat, Inc.'
        opts, args = getopt.getopt(sys.argv[1:], "O:")
        for o,v in opts:
            if o == "-O":
                orgName = v
        subject = args[0]
        random_num = args[1]
    except:
        print main.__doc__
        return 0

    try:
        CSR(subject, random_num, orgName).runTest()
        ret = True
    except:
        logging.error(traceback.format_exc())
        logging.error(main.__doc__)
        logging.debug("<BSTRAP component='RHEV_INSTALL' status='FAIL'/>")
        print "<BSTRAP component='RHEV_INSTALL' status='FAIL'/>"

        return 0
    else:
        if not ret:
            print "<BSTRAP component='RHEV_INSTALL' status='FAIL'/>"
            logging.debug("<BSTRAP component='RHEV_INSTALL' status='FAIL'/>")

        else:
            print "<BSTRAP component='RHEV_INSTALL' status='OK'/>"
            logging.debug("<BSTRAP component='RHEV_INSTALL' status='OK'/>")

    sys.stdout.flush()

if __name__ == "__main__":
    sys.exit(main())

