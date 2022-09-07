# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

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
