# Copyright 2008 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#
FILES=define.py utils.py constants.py \
      vm.py libvirtvm.py libvirtev.py \
      caps.py clientIF.py \
      hooks.py hooks/hooking.py \
      guestIF.py dsaversion.py \
      configNetwork.py SecureXMLRPCServer.py ksm.py \
      netinfo.py neterrors.py \
      kaxmlrpclib.py config.py logUtils.py \
      supervdsm.py supervdsmServer.py \
      libvirtconnection.py vdsmDebugPlugin.py \
      pthread.py betterThreading.py \

SCRIPTFILES=vdsm logCollector.sh \
            addNetwork delNetwork \
            get-conf-item set-conf-item \
            mk_sysprep_floppy \
            write-net-config vdsm-restore-net-config \
            vdsm-store-net-config \
            get-vm-pid respawn \
            prepare-vmchannel

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
	mkdir -p $(DESTDIR)$(VDSMDIR)
	mkdir -p $(DESTDIR)$(VDSMDIR)/storage
	mkdir -p $(DESTDIR)$(TRUSTSTORE)/{certs,keys}
	mkdir -p $(DESTDIR)$(VDSMLOGDIR)
	mkdir -p $(DESTDIR)$(VDSMRUNDIR)
	mkdir -p $(DESTDIR)$(VDSMLIBDIR)
	mkdir -p $(DESTDIR)$(VDSMLIBDIR)/netconfback
	mkdir -p $(DESTDIR)$(POOLSDATADIR)
	mkdir -p $(DESTDIR)$(BACKUPDIR)
	mkdir -p $(DESTDIR)$(BINDIR)
	mkdir -p $(DESTDIR)$(LIBEXECDIR)
	(for hook in $(HOOKS); do mkdir -p $(DESTDIR)$(LIBEXECDIR)/hooks/$$hook; done)
	mkdir -p $(DESTDIR)$(CONFDIR)
	mkdir -p $(DESTDIR)$(POOLDATADIR)
	mkdir -p $(DESTDIR)$(BACKUPDATADIR)
	mkdir -p $(DESTDIR)/etc/init.d/
	mkdir -p $(DESTDIR)/etc/udev/rules.d/
	mkdir -p $(DESTDIR)/var/lib/libvirt/qemu/channels/
	cp $(FILES) $(DESTDIR)$(VDSMDIR)
	cp $(SCRIPTFILES) $(DESTDIR)$(VDSMDIR)
	(cd storage; cp $(STORAGEFILES) $(DESTDIR)$(VDSMDIR)/storage)
	(cd storage; cp 12-vdsm-lvm.rules $(DESTDIR)$(UDEVDIR))
	cp logger.conf $(DESTDIR)$(CONFDIR)
	cp vdsmd $(DESTDIR)/etc/init.d/vdsmd
	(cd storage/protect; cp $(PROTECTFILES) $(DESTDIR)$(LIBEXECDIR))
	mkdir -p $(DESTDIR)/etc/sudoers.d/
	cp sudoers.vdsm $(DESTDIR)/etc/sudoers.d/50_vdsm
	mkdir -p $(DESTDIR)/etc/logrotate.d
	mkdir -p $(DESTDIR)/etc/cron.hourly
	mkdir -p $(DESTDIR)/etc/cron.d
	mkdir -p $(DESTDIR)$(SOSPLUGINDIR)
	cp -p vdsm-logrotate.conf $(DESTDIR)/etc/logrotate.d/vdsm
	cp -p vdsm-logrotate $(DESTDIR)/etc/cron.hourly
	cp -p vdsm-libvirt-logrotate $(DESTDIR)/etc/cron.d
	cp -p vdsm-sosplugin.py $(DESTDIR)$(SOSPLUGINDIR)/vdsm.py
	mkdir -p $(DESTDIR)$(MANDIR)/man8
	cp -p vdsmd.8 $(DESTDIR)$(MANDIR)/man8/
	mkdir -p $(DESTDIR)/etc/rwtab.d/
	cp -p vdsm.rwtab $(DESTDIR)/etc/rwtab.d/vdsm
	cp -p hooks/persist-vdsm-hooks $(DESTDIR)$(LIBEXECDIR)
	cp -p hooks/unpersist-vdsm-hook $(DESTDIR)$(LIBEXECDIR)

clean:
	$(RM) *~ *.pyc storage/protect/safelease vdsm.conf.sample
