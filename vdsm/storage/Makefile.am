#
# Copyright 2008-2011 Red Hat, Inc.
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

SUBDIRS = protect

vdsmstoragedir = $(vdsmdir)/storage
dist_vdsmstorage_PYTHON = \
	__init__.py \
	blockSD.py \
	blockVolume.py \
	devicemapper.py \
	dispatcher.py \
	domainMonitor.py \
	fileSD.py \
	fileUtils.py \
	fileVolume.py \
	fuser.py \
	hba.py \
	hsm.py \
	image.py \
	iscsi.py \
        iscsiadm.py \
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
	storageConstants.py \
	storage_exception.py \
	storage_mailbox.py \
        storageServer.py \
	taskManager.py \
	task.py \
	threadLocal.py \
	threadPool.py \
	volume.py

EXTRA_DIST = \
	12-vdsm-lvm.rules

check-local:
	python $(srcdir)/storage_exception.py
