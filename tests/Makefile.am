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
	common/validate_test.py \
	$(NULL)

device_modules = \
	devices/parsing/complex_vm_test.py \
	$(NULL)

virt_modules = virt/*_test.py

gluster_modules = gluster/*_test.py

test_modules = \
	API_test.py \
	alignmentscan_test.py \
	api_response_test.py \
	caps_test.py \
	clientif_test.py \
	cmdutils_test.py \
	config_test.py \
	cpu_profile_test.py \
	cpuinfo_test.py \
	domcaps_test.py \
	exception_test.py \
	executor_test.py \
	eventfd_test.py \
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
	osutils_test.py \
	passwords_test.py \
	permutation_test.py \
	response_test.py \
	rngsources_test.py \
	schedule_test.py \
	schemavalidation_test.py \
	sigutils_test.py \
	stompadapter_test.py \
	stompasyncclient_test.py \
	sysprep_test.py \
	taskset_test.py \
	testlib_test.py \
	tool_confmeta_test.py \
	tool_test.py \
	throttledlog_test.py \
	validate_test.py \
	vdsmapi_test.py \
	vdsmdumpchains_test.py \
	verify.py \
	vmapi_test.py \
	vmfakelib_test.py \
	vmTestsData.py \
	zombiereaper_test.py \
	$(NULL)

CLEANFILES = \
	run_tests_local.sh \
	$(NULL)

run_modules  = $(test_modules)
run_modules += $(device_modules)
run_modules += $(common_modules)

# Disable builtin (recursive) check target.
check-recursive:
	@true

.PHONY: check-virt
check-virt: run_tests_local.sh
	@echo '=== Running virt tests. ==='
	@$(top_srcdir)/tests/run_tests_local.sh $(virt_modules)

.PHONY: check
check: run_tests_local.sh
	@echo '=== Running check target with python3 interpreter version.              ==='
	@echo '=== To skip this step place "NOSE_EXCLUDE=.*" into your environment.    ==='
	@echo '=== Do not submit untested code!                                        ==='
	PYTHON_EXE=python3 \
		$(top_srcdir)/tests/run_tests_local.sh $(run_modules) && \
	if [ -n "$$NOSE_WITH_COVERAGE" ]; then \
		mv .coverage .coverage-nose; \
	fi

# Disable builtin %.out rule, overrwriting out files
%.out: ;

run_%.sh: run_%.sh.in
	@echo "  SED $@"; $(PATHSUBST) $< |$(CONFIGSUBST) >$@
	chmod a+x "$@"
