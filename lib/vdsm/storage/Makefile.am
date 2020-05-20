# Copyright 2017 Red Hat, Inc.
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
SUBDIRS = protect sdm

include $(top_srcdir)/build-aux/Makefile.subs

vdsmstoragedir = $(vdsmpylibdir)/storage

dist_vdsmstorage_PYTHON = \
	__init__.py \
	asyncevent.py \
	asyncutils.py \
	backends.py \
	blkdiscard.py \
	blockSD.py \
	blockVolume.py \
	blockdev.py \
	check.py \
	clusterlock.py \
	compat.py \
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
	lvmfilter.py \
	mailbox.py \
	managedvolume.py \
	managedvolumedb.py \
	merge.py \
	misc.py \
	monitor.py \
	mount.py \
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
	sanlock_direct.py \
	sd.py \
	sdc.py \
	securable.py \
	sp.py \
	spbackends.py \
	storageServer.py \
	sysfs.py \
	task.py \
	taskManager.py \
	threadPool.py \
	transientdisk.py \
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
