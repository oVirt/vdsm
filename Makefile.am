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

TESTS = pyflakes

test: pyflakes exceptions
	echo $(rpmrelease) $(rpmversion)

.PHONY: pyflakes exceptions
exceptions:
	python vdsm/storage/storage_exception.py | grep Collision && exit 1 || true

pyflakes:
	@git ls-files '*.py' | xargs pyflakes \
	    || (echo "Pyflakes errors or pyflakes not found"; exit 1)

.PHONY: srpm rpm
srpm: dist
	$(MKDIR_P) $(RPMTOP)/{RPMS,SRPMS,SOURCES,BUILD}
	rpmbuild -ts \
		--define="_topdir $(RPMTOP)" \
		--define="_sourcedir $(PWD)" $(DIST_ARCHIVES)

rpm: dist
	rpmbuild --define="_topdir $(RPMTOP)" -ta $(DIST_ARCHIVES)
