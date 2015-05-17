# Copyright 2008 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

include $(top_srcdir)/build-aux/Makefile.subs

dist_vdsmexec_SCRIPTS = \
	spmstop.sh \
	$(NULL)

nodist_vdsmexec_SCRIPTS = \
	spmprotect.sh \
	$(NULL)

EXTRA_DIST = \
	spmprotect.sh.in \
	$(NULL)

all-local: \
	$(nodist_vdsm_SCRIPTS) \
	$(NULL)
