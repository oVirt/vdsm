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
import deployUtil

VDSM_CONF_FILE = '/etc/vdsm/vdsm.conf'

def Reboot(act=1):
    """
        Reboot.
    """
    if act:
        print "<BSTRAP component='Reboot' status='OK'/>"
        deployUtil.reboot()
    else:
        print "<BSTRAP component='Reboot' status='FAIL'/>"
    sys.stdout.flush()

def main():
    """Usage: vds_bootstrap_complete.py  [-c vds_config_str] <random_num> [reboot]"""
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
        arg = args[1]
    except:
        arg = 1

    res = True
    try:
        res = deployUtil.instCert(rnum, VDSM_CONF_FILE)
        if res:
            res = deployUtil.setCoreDumpPath()

        if res:
            res = deployUtil.cleanAll(rnum)

        if res:
            res = deployUtil.setVdsConf(vds_config_str, VDSM_CONF_FILE)

        Reboot(arg)
    except:
        res = False

    if res:
        print "<BSTRAP component='RHEV_INSTALL' status='OK'/>"
    else:
        print "<BSTRAP component='RHEV_INSTALL' status='FAIL'/>"
    sys.stdout.flush()

if __name__ == "__main__":
    sys.exit(main())
