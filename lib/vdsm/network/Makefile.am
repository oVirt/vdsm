# Copyright 2015-2017 Red Hat, Inc.
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

SUBDIRS = configurators ip link netinfo netlink ovs tc nm

include $(top_srcdir)/build-aux/Makefile.subs

vdsmnetworkdir = $(vdsmpylibdir)/network
dist_vdsmnetwork_PYTHON = \
	__init__.py \
	api.py \
	errors.py \
	canonicalize.py \
	connectivity.py \
	dhclient_monitor.py \
	ifacetracking.py \
	ifacquire.py \
	initializer.py \
	ipwrapper.py \
	kernelconfig.py \
	legacy_switch.py \
	libvirt.py \
	models.py \
	netconfpersistence.py \
	netrestore.py \
	netswitch.py \
	netupgrade.py \
	restore_net_config.py \
	sourceroute.py \
	sysctl.py \
	utils.py \
	$(NULL)
