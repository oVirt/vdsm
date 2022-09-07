# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

include $(top_srcdir)/build-aux/Makefile.subs

SUBDIRS=api common tool profiling rpc network virt storage hook host metrics \
	 gluster supervdsm_api

dist_vdsmpylib_PYTHON = \
	__init__.py \
	alignmentScan.py \
	API.py \
	client.py \
	clientIF.py \
	constants.py \
	config.py \
	cpuinfo.py \
	dmidecodeUtil.py \
	executor.py \
	health.py \
	hugepages.py \
	jobs.py \
	jsonrpcvdscli.py \
	kvm2ovirt.py \
	logUtils.py \
	machinetype.py \
	moduleloader.py \
	momIF.py \
	mkimage.py \
	numa.py \
	osinfo.py \
	ppc64HardwareInfo.py \
	protocoldetector.py \
	schedule.py \
	sslutils.py \
	supervdsm_server.py \
	taskset.py \
	throttledlog.py \
	utils.py \
	v2v.py \
	vdsmd.py \
	virtsparsify.py \
	virtsysprep.py \
	$(NULL)

nodist_vdsmpylib_PYTHON = \
	$(NULL)

nodist_noinst_DATA= \
	vdsm.conf.sample \
	$(NULL)

CLEANFILES = \
	$(nodist_vdsmpylib_PYTHON) \
	$(nodist_noinst_DATA) \
	$(NULL)


EXTRA_DIST = \
	vdsm.conf.sample \
	$(NULL)

vdsm.conf.sample: Makefile $(srcdir)/common/config.py
	@echo "  CONFIG $@"; PYTHONPATH=$(srcdir)/../ $(PYTHON) -m "vdsm.common.config" > $@

install-data-local: \
	install-data-sample-vdsm-conf
	$(MKDIR_P) $(DESTDIR)$(vdsmrundir)/v2v

uninstall-local: \
	uninstall-data-sample-vdsm-conf

install-data-sample-vdsm-conf:
	$(MKDIR_P) $(DESTDIR)$(vdsmconfdir)
	$(INSTALL_DATA) vdsm.conf.sample \
		$(DESTDIR)$(vdsmconfdir)/vdsm.conf

uninstall-data-sample-vdsm-conf:
	$(RM) $(DESTDIR)$(vdsmconfdir)/vdsm.conf

all-local: \
	$(nodist_vdsmpylib_PYTHON) \
	$(nodist_noinst_DATA)
