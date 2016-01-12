#
# Copyright 2013 Hat, Inc.
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

SUBDIRS=netlink tool infra profiling

dist_vdsmpylib_PYTHON = \
	__init__.py \
	cmdutils.py \
	compat.py \
	concurrent.py \
	define.py \
	exception.py \
	executor.py \
	health.py \
	ipwrapper.py \
	jsonrpcvdscli.py \
	libvirtconnection.py \
	m2cutils.py \
	netconfpersistence.py \
	netinfo.py \
	password.py \
	pthread.py \
	qemuimg.py \
	response.py \
	schedule.py \
	sslcompat.py \
	sslutils.py \
	sysctl.py \
	taskset.py \
	udevadm.py \
	utils.py \
	vdscli.py \
	virtsparsify.py \
	xmlrpc.py \
	$(NULL)

nodist_vdsmpylib_PYTHON = \
	config.py \
	constants.py \
	$(NULL)

nodist_noinst_DATA= \
	vdsm.conf.sample \
	$(NULL)

CLEANFILES = \
	$(nodist_vdsmpylib_PYTHON) \
	$(nodist_noinst_DATA) \
	$(NULL)


EXTRA_DIST = \
	config.py.in \
	constants.py.in \
	vdsm.conf.sample \
	$(NULL)

vdsm.conf.sample: Makefile config.py
	@echo "  CONFIG $@"; $(PYTHON) $(builddir)/config.py > $@

install-data-local: \
	install-data-sample-vdsm-conf

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
