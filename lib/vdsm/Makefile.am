#
# Copyright 2013-2017 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
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
