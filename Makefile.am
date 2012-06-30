#
# Copyright 2008-2012 Red Hat, Inc.
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

SUBDIRS = vdsm vdsm_cli vds_bootstrap vdsm_reg vdsm_hooks tests vdsm-tool

include $(top_srcdir)/build-aux/Makefile.subs

# This is an *exception*, we ship also vdsm.spec so it's possible to build the
# rpm from the tarball.
EXTRA_DIST = \
	build-aux/pkg-version \
	vdsm.spec \
	vdsm.spec.in

CLEANFILES = \
	$(DIST_ARCHIVES)

if HOOKS
WITH_HOOKS = --define="with_hooks 1"
endif

# When fixing a file to conform with pep8 add it to the WL here so it will be
# checkd from now on
PEP8_WHITELIST = \
	tests \
	vdsm-tool \
	vdsm/API.py \
	vdsm/BindingXMLRPC.py \
	vdsm/SecureXMLRPCServer.py \
	vdsm/blkid.py \
	vdsm/caps.py \
	vdsm/clientIF.py \
	vdsm/config.py.in \
	vdsm/configNetwork.py \
	vdsm/constants.py.in \
	vdsm/debugPluginClient.py \
	vdsm/define.py \
	vdsm/exception.py \
	vdsm/gluster \
	vdsm/guestIF.py \
	vdsm/hooks.py \
	vdsm/kaxmlrpclib.py \
	vdsm/ksm.py \
	vdsm/libvirtev.py \
	vdsm/libvirtvm.py \
	vdsm/lsblk.py \
	vdsm/md_utils.py \
	vdsm/momIF.py \
	vdsm/neterrors.py \
	vdsm/netinfo.py \
	vdsm/parted_utils.py \
	vdsm/rest/BindingREST.py \
	vdsm/rest/Controller.py \
	vdsm/storage/blockVolume.py \
	vdsm/storage/devicemapper.py \
	vdsm/storage/domainMonitor.py \
	vdsm/storage/fileSD.py \
	vdsm/storage/fileUtils.py \
	vdsm/storage/fileVolume.py \
	vdsm/storage/fuser.py \
	vdsm/storage/hba.py \
	vdsm/storage/iscsiadm.py \
	vdsm/storage/imageRepository \
	vdsm/storage/localFsSD.py \
	vdsm/storage/lvm.py \
	vdsm/storage/misc.py \
	vdsm/storage/mount.py \
	vdsm/storage/multipath.py \
	vdsm/storage/nfsSD.py \
	vdsm/storage/outOfProcess.py \
	vdsm/storage/persistentDict.py \
	vdsm/storage/resourceFactories.py \
	vdsm/storage/sd.py \
	vdsm/storage/securable.py \
	vdsm/storage/storage_exception.py \
	vdsm/storage/task.py \
	vdsm/storage/taskManager.py \
	vdsm/storage/volume.py \
	vdsm/supervdsm.py \
	vdsm/supervdsmServer.py \
	vdsm/tc.py \
	vdsm/vdsm \
	vdsm/vdsmDebugPlugin.py \
	vdsm_cli \
	vdsm_hooks/fileinject \
	vdsm_hooks/promisc/after_vm_start.py \
	vdsm_hooks/scratchpad \
	vdsm_hooks/sriov/before_vm_start.py \
	vdsm_hooks/vhostmd \
	vdsm_reg/createDaemon.py \
	vdsm_reg/define.py \
	vds_bootstrap/miniyum.py \
	$(NULL)

PEP8_BLACKLIST = \
	restData.py

check-local:
	find . -path './.git' -prune -type f -o \
		-name '*.py' -o -name '*.py.in'  | xargs $(PYFLAKES)
	$(PEP8) --exclude="$(PEP8_BLACKLIST)" --filename '*.py,*.py.in' \
		$(PEP8_WHITELIST)
	@if test -f .gitignore; then \
	  for i in `git ls-files \*.in`; do \
	    if ! grep -q -x $${i%%.in} .gitignore; then \
	      echo "Missing $${i%%.in} in .gitignore"; exit 1; fi; \
	  done; \
	fi;

all-local: \
	vdsm.spec

.PHONY: srpm rpm

srpm: dist
	rpmbuild -ts $(if $(BUILDID),--define="extra_release .$(BUILDID)") $(DIST_ARCHIVES)

rpm: dist
	rpmbuild -ta $(if $(BUILDID),--define="extra_release .$(BUILDID)") \
				$(WITH_HOOKS) $(DIST_ARCHIVES)

dist-hook: gen-VERSION gen-ChangeLog
.PHONY: gen-VERSION gen-ChangeLog

# Generate the ChangeLog file and insert it into the directory
# we're about to use to create a tarball.
gen-ChangeLog:
	if test -d .git; then					\
	  $(top_srcdir)/build-aux/gitlog-to-changelog		\
	    > $(distdir)/cl-t;					\
	  rm -f $(distdir)/ChangeLog;				\
	  mv $(distdir)/cl-t $(distdir)/ChangeLog;		\
	fi

gen-VERSION:
	if test -d .git; then					\
	  $(top_srcdir)/build-aux/pkg-version --full		\
	    > $(distdir)/ve-t;					\
	  rm -f $(distdir)/VERSION;				\
	  mv $(distdir)/ve-t $(distdir)/VERSION;		\
	fi
