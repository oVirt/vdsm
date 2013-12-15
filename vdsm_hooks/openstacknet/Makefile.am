#
# Copyright 2011-2012 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# Refer to the README and COPYING files for full details of the license
#

constsfile=openstacknet_utils.py

include $(top_srcdir)/build-aux/Makefile.subs

nodist_noinst_DATA = \
	sudoers

CLEANFILES = \
	$(nodist_noinst_DATA)

EXTRA_DIST = \
	after_device_create.py \
	after_device_destroy.py \
	before_device_create.py \
	$(constsfile) \
	sudoers.in

install-data-hook:
	chmod 440 $(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm_hook_openstacknet

install-data-local: install-data-consts install-data-sudoers
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_device_create
	$(INSTALL_SCRIPT) $(srcdir)/after_device_create.py \
		$(DESTDIR)$(vdsmhooksdir)/after_device_create/50_openstacknet
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_device_destroy
	$(INSTALL_SCRIPT) $(srcdir)/after_device_destroy.py \
		$(DESTDIR)$(vdsmhooksdir)/after_device_destroy/50_openstacknet
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_device_migrate_destination
	$(INSTALL_SCRIPT) $(srcdir)/after_device_create.py \
		$(DESTDIR)$(vdsmhooksdir)/after_device_migrate_destination/50_openstacknet
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_nic_hotplug
	$(INSTALL_SCRIPT) $(srcdir)/after_device_create.py \
		$(DESTDIR)$(vdsmhooksdir)/after_nic_hotplug/50_openstacknet
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_nic_hotunplug
	$(INSTALL_SCRIPT) $(srcdir)/after_device_destroy.py \
		$(DESTDIR)$(vdsmhooksdir)/after_nic_hotunplug/50_openstacknet
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_device_create
	$(INSTALL_SCRIPT) $(srcdir)/before_device_create.py \
		$(DESTDIR)$(vdsmhooksdir)/before_device_create/50_openstacknet
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug
	$(INSTALL_SCRIPT) $(srcdir)/before_device_create.py \
		$(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug/50_openstacknet

uninstall-local: uninstall-data-consts uninstall-data-sudoers
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_device_create/50_openstacknet
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_device_destroy/50_openstacknet
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_device_migrate_destination/50_openstacknet
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_nic_hotplug/50_openstacknet
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_nic_hotunplug/50_openstacknet
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_device_create/50_openstacknet
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug/50_openstacknet

install-data-consts:
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_device_create
	$(INSTALL_SCRIPT) $(srcdir)/$(constsfile) \
		$(DESTDIR)$(vdsmhooksdir)/after_device_create/$(constsfile)
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_device_destroy
	$(INSTALL_SCRIPT) $(srcdir)/$(constsfile) \
		$(DESTDIR)$(vdsmhooksdir)/after_device_destroy/$(constsfile)
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_device_migrate_destination
	$(INSTALL_SCRIPT) $(srcdir)/$(constsfile) \
		$(DESTDIR)$(vdsmhooksdir)/after_device_migrate_destination/$(constsfile)
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_nic_hotplug
	$(INSTALL_SCRIPT) $(srcdir)/$(constsfile) \
		$(DESTDIR)$(vdsmhooksdir)/after_nic_hotplug/$(constsfile)
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_nic_hotunplug
	$(INSTALL_SCRIPT) $(srcdir)/$(constsfile) \
		$(DESTDIR)$(vdsmhooksdir)/after_nic_hotunplug/$(constsfile)
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_device_create
	$(INSTALL_SCRIPT) $(srcdir)/$(constsfile) \
		$(DESTDIR)$(vdsmhooksdir)/before_device_create/$(constsfile)
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug
	$(INSTALL_SCRIPT) $(srcdir)/$(constsfile) \
		$(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug/$(constsfile)

uninstall-data-consts:
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_device_create/$(constsfile)
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_device_destroy/$(constsfile)
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_device_migrate_destination/$(constsfile)
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_nic_hotplug/$(constsfile)
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_nic_hotunplug/$(constsfile)
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_device_create/$(constsfile)
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug/$(constsfile)

install-data-sudoers:
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/sudoers.d
	$(INSTALL_DATA) $(srcdir)/sudoers \
		$(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm_hook_openstacknet

uninstall-data-sudoers:
	$(RM) $(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm_hook_openstacknet
