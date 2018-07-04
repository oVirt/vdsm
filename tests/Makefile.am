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
	  $(NULL)

if CONTAINERS
SUBDIRS += containers
endif

common_modules = common/*_test.py

containers_modules = containers/*_test.py

device_modules = \
	devices/parsing/complex_vm_test.py \
	$(NULL)

network_modules = network/*_test.py

virt_modules = virt/*_test.py

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
	encoding_test.py \
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
	hooks_test.py \
	hostdev_test.py \
	hoststats_test.py \
	hugepages_test.py \
	hwinfo_test.py \
	jobs_test.py \
	jsonRpcClient_test.py \
	jsonrpc_test.py \
	loopback_test.py \
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
	ssl_test.py \
	stompadapter_test.py \
	stompasyncclient_test.py \
	stompasyncdispatcher_test.py \
	stomp_test.py \
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

blacklist_modules_python3 = \
	bridge_test.py \
	hooks_test.py \
	protocoldetector_test.py \
	stompasyncdispatcher_test.py \
	stomp_test.py \
	unicode_test.py \
	utils_test.py \
	$(NULL)

CLEANFILES = \
	$(nodist_vdsmtests_PYTHON) \
	$(dist_noinst_DATA) \
	$(dist_noinst_SCRIPTS) \
	$(NULL)

EXTRA_DIST = \
	makecert.sh \
	panic_helper.py \
	py-watch \
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
	cpu_map.xml \
	caps_numactl_4_nodes.out \
	domcaps_libvirt_s390x.out \
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
	loopback.py \
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
	./makecert.sh

run_modules  = $(test_modules)
if CONTAINERS
run_modules += $(containers_modules)
endif
run_modules += $(network_modules)
run_modules += $(device_modules)
run_modules += $(common_modules)

# Disable builtin (recursive) check target.
check-recursive:
	@true

check-net:
	@echo '=== Running networking tests. ==='
	@$(top_srcdir)/tests/run_tests_local.sh $(network_modules)

check-virt:
	@echo '=== Running virt tests. ==='
	@$(top_srcdir)/tests/run_tests_local.sh $(virt_modules)

check:
	@echo '=== Running tests.  To skip this step place NOSE_EXCLUDE=.* ==='
	@echo '=== into your environment.  Do not submit untested code!    ==='
	$(top_srcdir)/tests/run_tests_local.sh $(run_modules)
	if [ -n "$$NOSE_WITH_COVERAGE" ]; then \
		mv .coverage .coverage-nose-py2; \
	fi
	if [ "$(PYTHON3_SUPPORT)" == "1" ]; then \
		PYTHON_EXE="$(PYTHON3)" \
			$(top_srcdir)/tests/run_tests_local.sh \
			$(filter-out $(blacklist_modules_python3),$(run_modules)) && \
		if [ -n "$$NOSE_WITH_COVERAGE" ]; then \
		    mv .coverage .coverage-nose-py3; \
		fi \
	fi

# Disable builtin %.out rule, overrwriting out files
%.out: ;

run_%.sh: run_%.sh.in
	@echo "  SED $@"; $(PATHSUBST) $< |$(CONFIGSUBST) >$@
	chmod a+x "$@"
