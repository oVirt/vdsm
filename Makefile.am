# Copyright 2008 Red Hat, Inc. and/or its affiliates.
#
# Licensed to you under the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.  See the files README and
# LICENSE_GPL_v2 which accompany this distribution.
#

SUBDIRS = vdsm vdsm_cli vds_bootstrap vdsm_reg vdsm_hooks
EXTRA_DIST = vdsm.spec

rpmversion = @PACKAGE_VERSION@
rpmrelease = @PACKAGE_RELEASE@
RPMTOP = $(PWD)/rpmtop

check-local:
	pyflakes $(SUBDIRS)

.PHONY: srpm rpm

srpm: dist
	$(MKDIR_P) $(RPMTOP)/{RPMS,SRPMS,SOURCES,BUILD}
	rpmbuild -ts \
		--define="_topdir $(RPMTOP)" \
		--define="_sourcedir $(PWD)" $(DIST_ARCHIVES)

rpm: dist
	rpmbuild --define="_topdir $(RPMTOP)" -ta $(DIST_ARCHIVES)
