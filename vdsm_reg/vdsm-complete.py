#!/usr/bin/python
#
# Copyright 2008 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

import sys
import getopt
import logging
import logging.config
from time import strftime
import deployUtil

VDSM_CONF_FILE = '/etc/vdsm/vdsm.conf'
log_filename = '/var/log/vdsm-reg/vds_bootstrap_complete.'+strftime("%Y%m%d_%H%M%S")+'.log'
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(levelname)-8s %(message)s',
                    datefmt='%a, %d %b %Y %H:%M:%S',
                    filename=log_filename,
                    filemode='w')

def reboot(act=1):
    """ Reboot.
    """
    logging.debug("Reboot: started.")
    action = 'Reboot'
    message = 'Rebooting machine'

    if (act==1):
        deployUtil.reboot()
    else:
        action = 'Restart'
        message = 'Restarting vdsmd service'
        deployUtil.setService("vdsmd", "restart")

    print "<BSTRAP component='" + action + "' status='OK' message='" + message + "' />"
    sys.stdout.flush()
    logging.debug("Reboot: ended.")

def main():
    """Usage: ovirt-vdsm-complete.py  [-c vds_config_str] <random_num> [reboot]"""
    try:
        vds_config_str = None
        opts, args = getopt.getopt(sys.argv[1:], "c:")
        for o,v in opts:
            if o == "-c":
                # it should looks like: 'ssl=true;ksm_nice=5;images=/images/irsd'
                # without white spaces in it.
                vds_config_str = v

        rnum = args[0]
    except:
        print main.__doc__
        return 0
    try:
        act = args[1]
    except:
        act = 1

    fOK = True
    try:
        fOK = deployUtil.instCert(rnum, VDSM_CONF_FILE)
        if fOK:
            fOK = deployUtil.setCoreDumpPath()

        if fOK:
            fOK = deployUtil.cleanAll(rnum)

        if fOK:
            fOK = deployUtil.setVdsConf(vds_config_str, VDSM_CONF_FILE)

        if fOK:
            deployUtil.setService("vdsmd", "reconfigure")
            reboot(act)
    except:
        fOK = False

    if not fOK:
        print "<BSTRAP component='RHEV_INSTALL' status='FAIL'/>"
        logging.debug("<BSTRAP component='RHEV_INSTALL' status='FAIL'/>")

    else:
        print "<BSTRAP component='RHEV_INSTALL' status='OK'/>"
        logging.debug("<BSTRAP component='RHEV_INSTALL' status='OK'/>")

    sys.stdout.flush()

if __name__ == "__main__":
    sys.exit(main())
