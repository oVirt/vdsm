#
# Copyright 2012-2017 Red Hat, Inc.
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

include $(top_srcdir)/build-aux/Makefile.subs

SUBDIRS = \
	  common \
	  cpuinfo \
	  functional \
	  devices \
	  integration \
	  network \
	  storage \
	  virt \
	  gluster \
	  $(NULL)

common_modules = \
	common/cache_test.py \
	common/cmdutils_test.py \
	common/fileutils_test.py \
	common/function_test.py \
	common/hostutils_test.py \
	common/libvirtconnection_test.py \
	common/logutils_test.py \
	common/network_test.py \
	common/osutils_test.py \
	common/proc_test.py \
	common/properties_test.py \
	common/pthread_test.py \
	common/time_test.py \
	common/validate_test.py \
	$(NULL)

device_modules = \
	devices/parsing/complex_vm_test.py \
	$(NULL)

network_modules = network/*_test.py

virt_modules = virt/*_test.py

gluster_modules = gluster/*_test.py

test_modules = \
	API_test.py \
	alignmentscan_test.py \
	api_response_test.py \
	bridge_test.py \
	caps_test.py \
	client_test.py \
	clientif_test.py \
	cmdutils_test.py \
	config_test.py \
	cpu_profile_test.py \
	cpuinfo_test.py \
	domcaps_test.py \
	exception_test.py \
	executor_test.py \
	eventfd_test.py \
	fallocate_test.py \
	filecontrol_test.py \
	fuser_test.py \
	glob_test.py \
	gluster_cli_test.py \
	gluster_exception_test.py \
	glusterTestData.py \
	gluster_thinstorage_test.py \
	hostdev_test.py \
	hoststats_test.py \
	hugepages_test.py \
	hwinfo_test.py \
	jobs_test.py \
	jsonRpcClient_test.py \
	jsonrpc_test.py \
	mkimage_test.py \
	modprobe.py \
	moduleloader_test.py \
	monkeypatch_test.py \
	mom_test.py \
	mompolicy_test.py \
	osinfo_test.py \
	osutils_test.py \
	passwords_test.py \
	permutation_test.py \
	protocoldetector_test.py \
	response_test.py \
	rngsources_test.py \
	schedule_test.py \
	schemavalidation_test.py \
	sigutils_test.py \
	sparsify_test.py \
	stompadapter_test.py \
	stompasyncclient_test.py \
	stompasyncdispatcher_test.py \
	stomp_test.py \
	sysprep_test.py \
	taskset_test.py \
	testlib_test.py \
	tool_confmeta_test.py \
	tool_test.py \
	throttledlog_test.py \
	unicode_test.py \
	utils_test.py \
	validate_test.py \
	vdsmapi_test.py \
	vdsmdumpchains_test.py \
	verify.py \
	vmapi_test.py \
	vmfakelib_test.py \
	vmTestsData.py \
	zombiereaper_test.py \
	$(NULL)

blacklisted_modules_py36 = \
	bridge_test.py \
	protocoldetector_test.py \
	stompasyncdispatcher_test.py \
	stomp_test.py \
	unicode_test.py \
	utils_test.py \
	$(NULL)

blacklisted_modules_py37 = \
	$(blacklisted_modules_py36) \
	$(NULL)

CLEANFILES = \
	$(nodist_vdsmtests_PYTHON) \
	$(dist_noinst_DATA) \
	$(dist_noinst_SCRIPTS) \
	$(NULL)

EXTRA_DIST = \
	conf/svdsm.logger.conf \
	makecert.sh \
	panic_helper.py \
	py-watch \
	profile \
	profile-analyze \
	tests_child.py \
	$(NULL)

server_certificates = \
	server.crt \
	server.csr \
	server.key \
	server.p12

other_certificates = \
	other.crt \
	other.csr \
	other.key

dist_noinst_DATA = \
	$(server_certificates) \
	$(other_certificates)

dist_noinst_SCRIPTS = \
	run_tests_local.sh

dist_vdsmtests_DATA = \
	README \
	cpu_info.out \
	caps_libvirt_intel_E5649.out \
	caps_libvirt_amd_6274.out \
	caps_libvirt_ibm_S822L.out \
	caps_libvirt_ibm_S822L_le.out \
	caps_libvirt_intel_E31220.out \
	caps_libvirt_intel_E5606.out \
	caps_libvirt_intel_i73770.out \
	caps_libvirt_intel_i73770_nosnap.out \
	caps_libvirt_multiqemu.out \
	caps_libvirt_s390x.out \
	caps_numactl_4_nodes.out \
	domcaps_libvirt_ppc64le.out \
	domcaps_libvirt_s390x.out \
	domcaps_libvirt_x86_64.out \
	fake-virt-v2v.err \
	fake-virt-v2v.out \
	glusterGeoRepStatus.xml \
	glusterSnapshotConfig.xml \
	glusterSnapshotRestore.xml \
	glusterVolumeGeoRepConfigList.xml \
	glusterSnapshotList.xml \
	glusterVolumeSnapshotList.xml \
	glusterSnapshotListEmpty.xml \
	glusterVolumeSnapshotListEmpty.xml \
	glusterVolumeProfileInfo.xml \
	glusterVolumeProfileInfoNfs.xml \
	glusterVolumeRebalanceStatus.xml \
	glusterVolumeRemoveBricksStatus.xml \
	glusterVolumeTasks.xml \
        glusterVolumeHealInfo.xml \
	mem_info.out \
	toolTests_CCPP.conf \
	toolTests_abrt-action-save-package-data.conf \
	toolTests_abrt.conf \
	toolTests_empty.conf \
	toolTests_lconf_ssl.conf \
	toolTests_libvirtd.conf \
	toolTests_mtab_1g \
	toolTests_mtab_default \
	toolTests_mtab_nohugetlbfs \
	toolTests_qemu_sanlock.conf \
	toolTests_qemu_ssl.conf \
	toolTests_vdsm_no_ssl.conf \
	toolTests_vdsm_ssl.conf \
	toolTests_vmcore.conf \
	$(NULL)

dist_vdsmtests_PYTHON = \
	$(test_modules) \
	apiData.py \
	check_imports.py \
	hookValidation.py \
	fakelib.py \
	hostdevlib.py \
	monkeypatch.py \
	testlib.py \
	testrunner.py \
	testValidation.py \
	vmfakecon.py \
	vmfakelib.py \
	$(NULL)

dist_vdsmtests_SCRIPTS = \
	check_distpkg.sh \
	check_rpms.sh \
	fake-virt-sysprep \
	fake-virt-v2v \
	fake-ssh-add \
	fake-ssh-agent \
	mom_policy_validator.py \
	run_tests.sh \
	$(NULL)

all-local: \
	$(nodist_vdsmtests_PYTHON)

$(server_certificates) $(other_certificates): make_certificates

.PHONY: make_certificates
make_certificates: makecert.sh
	$(srcdir)/makecert.sh

run_modules  = $(test_modules)
run_modules += $(network_modules)
run_modules += $(device_modules)
run_modules += $(common_modules)

define define_check_vars
check-$(1): PYTHON_EXE = $(2)
check-$(1): PYTHON_SHORT_NAME = $(1)
check-$(1): BLACKLISTED_MODULES = $(blacklisted_modules_$(1))
endef

# Determine variable values for all 'check-py*' targets
# (i.e. 'check-py36: PYTHON_EXE = python3.6').
$(foreach i,\
          $(shell seq $(words $(VDSM_SUPPORTED_PY_VERSIONS))),\
		  $(eval $(call define_check_vars,$(word $(i), $(VDSM_SUPPORTED_PY_SHORT_VERSIONS)),$(word $(i), $(VDSM_SUPPORTED_PY_VERSIONS)))))

# Disable builtin (recursive) check target.
check-recursive:
	@true

.PHONY: check-net
check-net:
	@echo '=== Running networking tests. ==='
	@$(top_srcdir)/tests/run_tests_local.sh $(network_modules)

.PHONY: check-virt
check-virt:
	@echo '=== Running virt tests. ==='
	@$(top_srcdir)/tests/run_tests_local.sh $(virt_modules)

.PHONY: check-target
check-target: check-$(VDSM_TARGET_PY_SHORT_VERSION)

.PHONY: check-all
check-all: $(VDSM_SUPPORTED_PY_SHORT_VERSIONS:%=check-%)

.PHONY: check
check: check-all

# To run checks for specific interpreter version use
# i.e. 'make -C tests check-py36' from root dir
check-%:
	@echo '=== Running "$(@)" target with "$(PYTHON_EXE)" interpreter version.   ==='
	@echo '=== To skip this step place "NOSE_EXCLUDE=.*" into your environment.    ==='
	$(if $(BLACKLISTED_MODULES),@printf '=== Disabling blacklisted module: %37s ===\n' $(shell echo $(BLACKLISTED_MODULES)),)
	@echo '=== Do not submit untested code!                                        ==='
	PYTHON_EXE=$(PYTHON_EXE) \
		$(top_srcdir)/tests/run_tests_local.sh \
		$(filter-out $(BLACKLISTED_MODULES),$(run_modules)) && \
	if [ -n "$$NOSE_WITH_COVERAGE" ]; then \
		mv .coverage .coverage-nose-$(PYTHON_SHORT_NAME); \
	fi

# Disable builtin %.out rule, overrwriting out files
%.out: ;

run_%.sh: run_%.sh.in
	@echo "  SED $@"; $(PATHSUBST) $< |$(CONFIGSUBST) >$@
	chmod a+x "$@"
