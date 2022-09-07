# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

include $(top_srcdir)/build-aux/Makefile.subs

SUBDIRS = network

vdsmcommondir = $(vdsmpylibdir)/common

nodist_vdsmcommon_PYTHON = \
	$(srcdir)/config.py \
	$(srcdir)/constants.py \
	$(srcdir)/dsaversion.py \
	$(NULL)

dist_vdsmcommon_PYTHON = \
	$(filter-out $(nodist_vdsmcommon_PYTHON), $(wildcard $(srcdir)/*.py))
	$(NULL)

EXTRA_DIST = \
	config.py.in \
	constants.py.in \
	dsaversion.py.in \
	$(NULL)

CLEANFILES = \
	$(nodist_vdsmcommon_PYTHON) \
	$(NULL)

all-local: \
	$(nodist_vdsmcommon_PYTHON) \
	$(NULL)
