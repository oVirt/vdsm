# Copyright 2008 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

INSTALL=install

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
	$(INSTALL) -Dd $(DESTDIR)$(VDSMDIR)/storage/protect
	$(INSTALL) -Dd $(DESTDIR)$(TRUSTSTORE)/{certs,keys}
	$(INSTALL) -Dd $(DESTDIR)$(VDSMLOGDIR)
	$(INSTALL) -Dd $(DESTDIR)$(VDSMRUNDIR)
	$(INSTALL) -Dd $(DESTDIR)$(VDSMLIBDIR)/netconfback
	$(INSTALL) -Dd $(DESTDIR)$(POOLSDATADIR)
	$(INSTALL) -Dd $(DESTDIR)$(BACKUPDIR)
	$(INSTALL) -Dd $(DESTDIR)$(POOLDATADIR)
	$(INSTALL) -Dd $(DESTDIR)/etc/init.d
	$(INSTALL) -Dd $(DESTDIR)/etc/udev/rules.d
	$(INSTALL) -Dd -m 775 $(DESTDIR)/var/lib/libvirt/qemu/channels
	(for hook in $(HOOKS); do \
	    $(INSTALL) -Dd $(DESTDIR)$(LIBEXECDIR)/hooks/$$hook; \
	done)
	$(INSTALL) -Dm 644 $(FILES) $(DESTDIR)$(VDSMDIR)
	$(INSTALL) -Dm 755 $(SCRIPTFILES) $(DESTDIR)$(VDSMDIR)
	(cd storage; \
	    $(INSTALL) -Dm 644 $(STORAGEFILES) $(DESTDIR)$(VDSMDIR)/storage)
	(cd storage/protect; \
	    $(INSTALL) -Dm 755 $(PROTECTFILES) $(DESTDIR)$(LIBEXECDIR))
	$(INSTALL) -Dm 644 storage/12-vdsm-lvm.rules $(DESTDIR)$(UDEVDIR)
	$(INSTALL) -Dm 644 logger.conf $(DESTDIR)$(CONFDIR)/logger.conf
	$(INSTALL) -Dm 755 vdsmd $(DESTDIR)/etc/init.d/vdsmd
	$(INSTALL) -Dm 440 sudoers.vdsm $(DESTDIR)/etc/sudoers.d/50_vdsm
	$(INSTALL) -Dm 644 vdsm-logrotate.conf $(DESTDIR)/etc/logrotate.d/vdsm
	$(INSTALL) -Dm 755 vdsm-logrotate $(DESTDIR)/etc/cron.hourly/vdsm-logrotate
	$(INSTALL) -Dm 755 vdsm-libvirt-logrotate $(DESTDIR)/etc/cron.d/vdsm-libvirt-logrotate
	$(INSTALL) -Dm 644 vdsm-sosplugin.py $(DESTDIR)$(SOSPLUGINDIR)/vdsm.py
	$(INSTALL) -Dm 644 vdsmd.8 $(DESTDIR)$(MANDIR)/man8/vdsmd.8
	$(INSTALL) -Dm 644 vdsm.rwtab $(DESTDIR)/etc/rwtab.d/vdsm
	$(INSTALL) -Dm 755 hooks/persist-vdsm-hooks $(DESTDIR)$(LIBEXECDIR)/persist-vdsm-hooks
	$(INSTALL) -Dm 755 hooks/unpersist-vdsm-hook $(DESTDIR)$(LIBEXECDIR)/unpersist-vdsm-hook

clean:
	$(RM) *~ *.pyc storage/protect/safelease vdsm.conf.sample
