# Copyright 2008 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#
FILES=define.py utils.py constants.py vm.py libvirtvm.py libvirtev.py vdsm \
      caps.py clientIF.py \
      hooks.py hooks/hooking.py \
      guestIF.py dsaversion.py logCollector.sh \
      configNetwork.py addNetwork delNetwork \
      SecureXMLRPCServer.py ksm.py netinfo.py neterrors.py \
      get-conf-item set-conf-item \
      kaxmlrpclib.py config.py \
      write-net-config vdsm-restore-net-config vdsm-store-net-config \
      mk_sysprep_floppy get-vm-pid respawn logUtils.py \
      supervdsm.py supervdsmServer.py prepare-vmchannel \
      libvirtconnection.py vdsmDebugPlugin.py
FILES += pthread.py
FILES += betterThreading.py

STORAGEFILES=storageConstants.py dispatcher.py storage_exception.py \
             __init__.py \
             sd.py sp.py blockSD.py fileSD.py hsm.py safelease.py spm.py \
             image.py volume.py blockVolume.py fileVolume.py taskManager.py\
             resourceManager.py storage_connection.py fileUtils.py misc.py hba.py \
             lvm.py iscsi.py multipath.py threadPool.py task.py \
             storage_mailbox.py sdc.py sdf.py persistentDict.py \
             threadLocal.py nfsSD.py localFsSD.py resourceFactories.py \
	     outOfProcess.py processPool.py devicemapper.py

PROTECTFILES=spmprotect.sh spmstop.sh safelease

VDSMDIR=/usr/share/vdsm
VDSMLOGDIR=/var/log/vdsm
VDSMRUNDIR=/var/run/vdsm
VDSMLIBDIR=/var/lib/vdsm
LOGROTATEDIR=/etc/logrotate.d
SOSPLUGINDIR=/usr/lib/python/site-packages/sos/plugins
POOLSDATADIR=$(VDSMRUNDIR)/pools
BACKUPDIR=$(VDSMLOGDIR)/backup
BINDIR=$(VDSMDIR)
LIBEXECDIR=/usr/libexec/vdsm
HOOKSDIR=$(LIBEXECDIR)/hooks
CONFDIR=$(VDSMDIR)
TRUSTSTORE=$(VDSMRUNDIR)/ts
MANDIR=/usr/share/man
UDEVDIR=/etc/udev/rules.d

CFLAGS=-Wall -O

all: storage/protect/safelease

# TODO fix this nonsense with autoconf
.PHONY: fixpaths
sudoers.vdsm: fixpaths sudoers.vdsm.in
	python substitute_constants.py - < sudoers.vdsm.in > $@

vdsm.conf.sample: fixpaths config.py
	python mk_vdsm.conf.sample.py > vdsm.conf.sample

fixpaths:
	sed -i "s:@CONFDIR@:$(CONFDIR):; \
	        s:@VDSMLOGDIR@:$(VDSMLOGDIR):; \
	        s:@VDSMDIR@:$(VDSMDIR):; \
	        s:@HOOKSDIR@:$(HOOKSDIR):; \
	        s:@VDSMRUNDIR@:$(VDSMRUNDIR):; \
	        s:@VDSMLIBDIR@:$(VDSMLIBDIR):; \
	        s:@POOLSDIR@:$(POOLSDATADIR):; \
	        s:@BACKUPDIR@:$(BACKUPDIR):; \
	        s:@LIBEXECDIR@:$(LIBEXECDIR):; \
	        s:@TRUSTSTORE@:$(TRUSTSTORE):" constants.py vdsm-sosplugin.py
	python substitute_constants.py mk_sysprep_floppy logger.conf vdsmd \
			    vdsm-store-net-config vdsm-restore-net-config

HOOKS=before_vm_start after_vm_start before_vm_cont after_vm_cont \
      before_vm_pause after_vm_pause \
      before_vm_hibernate after_vm_hibernate \
      before_vm_dehibernate after_vm_dehibernate \
      before_vm_migrate_source after_vm_migrate_source \
      before_vm_migrate_destination after_vm_migrate_destination \
      after_vm_destroy \
      before_vdsm_start after_vdsm_stop

install: all sudoers.vdsm vdsm.conf.sample
	mkdir -p $(PREFIX)$(VDSMDIR)
	mkdir -p $(PREFIX)$(VDSMDIR)/storage
	mkdir -p $(PREFIX)$(TRUSTSTORE)/{certs,keys}
	mkdir -p $(PREFIX)$(VDSMLOGDIR)
	mkdir -p $(PREFIX)$(VDSMRUNDIR)
	mkdir -p $(PREFIX)$(VDSMLIBDIR)
	mkdir -p $(PREFIX)$(VDSMLIBDIR)/netconfback
	mkdir -p $(PREFIX)$(POOLSDATADIR)
	mkdir -p $(PREFIX)$(BACKUPDIR)
	mkdir -p $(PREFIX)$(BINDIR)
	mkdir -p $(PREFIX)$(LIBEXECDIR)
	(for hook in $(HOOKS); do mkdir -p $(PREFIX)$(LIBEXECDIR)/hooks/$$hook; done)
	mkdir -p $(PREFIX)$(CONFDIR)
	mkdir -p $(PREFIX)$(POOLDATADIR)
	mkdir -p $(PREFIX)$(BACKUPDATADIR)
	mkdir -p $(PREFIX)/etc/init.d/
	mkdir -p $(PREFIX)/etc/udev/rules.d/
	mkdir -p $(PREFIX)/var/lib/libvirt/qemu/channels/
	cp $(FILES) $(PREFIX)$(VDSMDIR)
	(cd storage; cp $(STORAGEFILES) $(PREFIX)$(VDSMDIR)/storage)
	(cd storage; cp 12-vdsm-lvm.rules $(PREFIX)$(UDEVDIR))
	cp logger.conf $(PREFIX)$(CONFDIR)
	cp vdsmd $(PREFIX)/etc/init.d/vdsmd
	(cd storage/protect; cp $(PROTECTFILES) $(PREFIX)$(LIBEXECDIR))
	mkdir -p $(PREFIX)/etc/sudoers.d/
	cp sudoers.vdsm $(PREFIX)/etc/sudoers.d/50_vdsm
	mkdir -p $(PREFIX)/etc/logrotate.d
	mkdir -p $(PREFIX)/etc/cron.hourly
	mkdir -p $(PREFIX)$(SOSPLUGINDIR)
	cp -p vdsm-logrotate.conf $(PREFIX)/etc/logrotate.d/vdsm
	cp -p vdsm-logrotate $(PREFIX)/etc/cron.hourly
	cp -p vdsm-sosplugin.py $(PREFIX)$(SOSPLUGINDIR)/vdsm.py
	mkdir -p $(PREFIX)$(MANDIR)/man8
	cp -p vdsmd.8 $(PREFIX)$(MANDIR)/man8/
	mkdir -p $(PREFIX)/etc/rwtab.d/
	cp -p vdsm.rwtab $(PREFIX)/etc/rwtab.d/vdsm
	cp -p hooks/persist-vdsm-hooks $(PREFIX)$(LIBEXECDIR)
	cp -p hooks/unpersist-vdsm-hook $(PREFIX)$(LIBEXECDIR)

clean:
	$(RM) *~ *.pyc storage/protect/safelease vdsm.conf.sample
