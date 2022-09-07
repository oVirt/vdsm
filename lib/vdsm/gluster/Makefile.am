# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

include $(top_srcdir)/build-aux/Makefile.subs

vdsmglusterdir = $(vdsmpylibdir)/gluster

common = \
	__init__.py \
	cli.py \
	exception.py \
	$(NULL)

if GLUSTER_MGMT
gluster_mgmt = \
	api.py \
	apiwrapper.py \
	events.py \
	fence.py \
	fstab.py \
	gfapi.py \
	hooks.py \
	services.py \
	storagedev.py \
	tasks.py \
	thinstorage.py \
	$(NULL)
endif

dist_vdsmgluster_PYTHON = $(common) $(gluster_mgmt)
