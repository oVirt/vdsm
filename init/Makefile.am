# SPDX-FileCopyrightText: 2013 IBM, Inc.
# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

SUBDIRS = systemd

include $(top_srcdir)/build-aux/Makefile.subs

nodist_vdsmexec_SCRIPTS = \
	vdsmd_init_common.sh \
	$(NULL)

dist_vdsmexec_SCRIPTS = \
	daemonAdapter \
	$(NULL)

CLEANFILES = \
	config.log \
	$(nodist_vdsmexec_SCRIPTS) \
	$(NULL)

EXTRA_DIST = \
	vdsmd_init_common.sh.in \
	$(NULL)
