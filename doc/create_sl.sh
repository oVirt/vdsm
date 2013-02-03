#!/bin/bash

# Adds a symoblic link for vdscli in vdsm if not present.
VDSCLI=$(readlink -f "../vdsm/vdscli.py")
if [ ! -e "$VDSCLI" ] ; then
   ln -s ../vdsm_cli/vdscli.py "$VDSCLI"
fi
