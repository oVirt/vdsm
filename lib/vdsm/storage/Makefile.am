# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

SUBDIRS = protect sdm

include $(top_srcdir)/build-aux/Makefile.subs

vdsmstoragedir = $(vdsmpylibdir)/storage

dist_vdsmstorage_PYTHON = \
	__init__.py \
	asyncevent.py \
	asyncutils.py \
	backends.py \
	bitmaps.py \
	blkdiscard.py \
	blockSD.py \
	blockVolume.py \
	blockdev.py \
	check.py \
	clusterlock.py \
	constants.py \
	curlImgWrap.py \
	devicemapper.py \
	directio.py \
	dispatcher.py \
	dmsetup.py \
	exception.py \
	fallocate.py \
	fileSD.py \
	fileUtils.py \
	fileVolume.py \
	formatconverter.py \
	fsutils.py \
	fuser.py \
	glance.py \
	glusterSD.py \
	glusterVolume.py \
	guarded.py \
	hba.py \
	hsm.py \
	image.py \
	imageSharing.py \
	imagetickets.py \
	iscsi.py \
	iscsiadm.py \
	localFsSD.py \
	lvm.py \
	lvmconf.py \
	lvmdevices.py \
	lvmfilter.py \
	lsof.py \
	mailbox.py \
	managedvolume.py \
	managedvolumedb.py \
	merge.py \
	misc.py \
	monitor.py \
	mount.py \
	mpathconf.py \
	mpathhealth.py \
	multipath.py \
	nbd.py \
	nfsSD.py \
	nos_brick.py \
	operation.py \
	outOfProcess.py \
	persistent.py \
	qemuimg.py \
	resourceFactories.py \
	resourceManager.py \
	rwlock.py \
	sanlockconf.py \
	sanlock_direct.py \
	sd.py \
	sdc.py \
	securable.py \
	sp.py \
	spbackends.py \
	spwd.py \
	storageServer.py \
	sysfs.py \
	task.py \
	taskManager.py \
	threadPool.py \
	transientdisk.py \
	utils.py \
	validators.py \
	volume.py \
	volumemetadata.py \
	workarounds.py \
	xlease.py \
	$(NULL)

dist_vdsmexec_SCRIPTS = \
	curl-img-wrap \
	fc-scan \
	managedvolume-helper
	$(NULL)

nodist_vdsmstorage_DATA = \
	lvm.env \
	$(NULL)

EXTRA_DIST = \
	lvm.env.in \
	$(NULL)

all: vdsm-lvm.rules

vdsm-lvm.rules: vdsm_lvm_rules.template
	python3 $< > $@

install-data-local: vdsm-lvm.rules
	$(MKDIR_P) $(DESTDIR)/usr/lib/udev/rules.d
	$(INSTALL_DATA) vdsm-lvm.rules $(DESTDIR)/usr/lib/udev/rules.d/12-vdsm-lvm.rules

uninstall-local:
	$(RM) $(DESTDIR)/usr/lib/udev/rules.d/12-vdsm-lvm.rules
