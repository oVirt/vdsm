# Copyright 2008 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

SUBDIRS = protect

vdsmstoragedir = $(vdsmdir)/storage
dist_vdsmstorage_DATA = \
	__init__.py \
	blockSD.py \
	blockVolume.py \
	devicemapper.py \
	dispatcher.py \
	fileSD.py \
	fileUtils.py \
	fileVolume.py \
	fuser.py \
	hba.py \
	hsm.py \
	image.py \
	iscsi.py \
	localFsSD.py \
	lvm.py \
	misc.py \
	mount.py \
	multipath.py \
	nfsSD.py \
	outOfProcess.py \
	persistentDict.py \
	processPool.py \
	resourceFactories.py \
	resourceManager.py \
	safelease.py \
	sdc.py \
	sd.py \
	securable.py \
	sp.py \
	storage_connection.py \
	storageConstants.py \
	storage_exception.py \
	storage_mailbox.py \
	taskManager.py \
	task.py \
	threadLocal.py \
	threadPool.py \
	volume.py

EXTRA_DIST = \
	12-vdsm-lvm.rules

check-local:
	python $(srcdir)/storage_exception.py
