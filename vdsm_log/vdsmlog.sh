#!/bin/bash

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

ssh $1 "cat /var/log/vdsm/vdsm.log" | source-highlight -f esc --style-file=vdsmlog.style --lang-def=vdsmlog.lang | less -R

