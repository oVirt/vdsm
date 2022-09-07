# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

constsfile=openstacknet_utils.py

include $(top_srcdir)/build-aux/Makefile.subs

nodist_noinst_DATA = \
	sudoers

CLEANFILES = \
	$(nodist_noinst_DATA)

EXTRA_DIST = \
	after_device_create.py \
	after_get_caps.py \
	after_vm_start.py \
	before_device_create.py \
	$(constsfile) \
	sudoers.in

install-data-hook:
	chmod 440 $(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm_hook_openstacknet

install-data-local: install-data-consts install-data-sudoers
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_device_create
	$(INSTALL_SCRIPT) $(srcdir)/after_device_create.py \
		$(DESTDIR)$(vdsmhooksdir)/after_device_create/50_openstacknet
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_device_migrate_destination
	$(INSTALL_SCRIPT) $(srcdir)/after_device_create.py \
		$(DESTDIR)$(vdsmhooksdir)/after_device_migrate_destination/50_openstacknet
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_nic_hotplug
	$(INSTALL_SCRIPT) $(srcdir)/after_device_create.py \
		$(DESTDIR)$(vdsmhooksdir)/after_nic_hotplug/50_openstacknet
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_vm_start
	$(INSTALL_SCRIPT) $(srcdir)/after_vm_start.py \
		$(DESTDIR)$(vdsmhooksdir)/after_vm_start/50_openstacknet
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_device_create
	$(INSTALL_SCRIPT) $(srcdir)/before_device_create.py \
		$(DESTDIR)$(vdsmhooksdir)/before_device_create/50_openstacknet
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug
	$(INSTALL_SCRIPT) $(srcdir)/before_device_create.py \
		$(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug/50_openstacknet
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_get_caps
	$(INSTALL_SCRIPT) $(srcdir)/after_get_caps.py \
		$(DESTDIR)$(vdsmhooksdir)/after_get_caps/50_openstacknet

uninstall-local: uninstall-data-consts uninstall-data-sudoers
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_device_create/50_openstacknet
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_device_migrate_destination/50_openstacknet
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_nic_hotplug/50_openstacknet
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_vm_start/50_openstacknet
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_device_create/50_openstacknet
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug/50_openstacknet
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_get_caps/50_openstacknet

install-data-consts:
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_device_create
	$(INSTALL_SCRIPT) $(srcdir)/$(constsfile) \
		$(DESTDIR)$(vdsmhooksdir)/after_device_create/$(constsfile)
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_device_migrate_destination
	$(INSTALL_SCRIPT) $(srcdir)/$(constsfile) \
		$(DESTDIR)$(vdsmhooksdir)/after_device_migrate_destination/$(constsfile)
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_nic_hotplug
	$(INSTALL_SCRIPT) $(srcdir)/$(constsfile) \
		$(DESTDIR)$(vdsmhooksdir)/after_nic_hotplug/$(constsfile)

	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_vm_start
	$(INSTALL_SCRIPT) $(srcdir)/$(constsfile) \
		$(DESTDIR)$(vdsmhooksdir)/after_vm_start/$(constsfile)
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_device_create
	$(INSTALL_SCRIPT) $(srcdir)/$(constsfile) \
		$(DESTDIR)$(vdsmhooksdir)/before_device_create/$(constsfile)
	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug
	$(INSTALL_SCRIPT) $(srcdir)/$(constsfile) \
		$(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug/$(constsfile)

	$(MKDIR_P) $(DESTDIR)$(vdsmhooksdir)/after_get_caps
	$(INSTALL_SCRIPT) $(srcdir)/$(constsfile) \
		$(DESTDIR)$(vdsmhooksdir)/after_get_caps/$(constsfile)

uninstall-data-consts:
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_device_create/$(constsfile)
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_device_migrate_destination/$(constsfile)
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_nic_hotplug/$(constsfile)
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_vm_start/$(constsfile)
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_device_create/$(constsfile)
	$(RM) $(DESTDIR)$(vdsmhooksdir)/before_nic_hotplug/$(constsfile)
	$(RM) $(DESTDIR)$(vdsmhooksdir)/after_get_caps/$(constsfile)

install-data-sudoers:
	$(MKDIR_P) $(DESTDIR)$(sysconfdir)/sudoers.d
	$(INSTALL_DATA) ./sudoers \
		$(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm_hook_openstacknet

uninstall-data-sudoers:
	$(RM) $(DESTDIR)$(sysconfdir)/sudoers.d/50_vdsm_hook_openstacknet
