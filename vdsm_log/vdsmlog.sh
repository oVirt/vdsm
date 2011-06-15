#!/bin/bash
ssh $1 "cat /var/log/vdsm/vdsm.log" | source-highlight -f esc --style-file=vdsmlog.style --lang-def=vdsmlog.lang | less -R

