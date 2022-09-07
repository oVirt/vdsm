# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

SUBDIRS=configurators

include $(top_srcdir)/build-aux/Makefile.subs

dist_vdsmtool_PYTHON = \
	__init__.py \
	config_lvm_filter.py \
	confmeta.py \
	confutils.py \
	dummybr.py \
	dump_volume_chains.py \
	network.py \
	nwfilter.py \
	configfile.py \
	configurator.py \
	register.py \
	service.py \
	transient.py \
	upgrade.py \
	vdsm-id.py \
	vdsm_config.py \
	common.py \
	check_volume_leases.py \
	xleases.py \
	$(NULL)

CLEANFILES = \
	config.log \
	$(nodist_vdsmtool_PYTHON) \
	$(nodist_vdsmexec_SCRIPTS) \
	$(NULL)

all-local: \
	$(nodist_vdsmtool_PYTHON) \
	$(nodist_vdsmexec_SCRIPTS) \
	$(NULL)
