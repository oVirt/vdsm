# Copyright 2021 Red Hat, Inc.
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
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Refer to the README and COPYING files for full details of the license
#

CONTAINER_CMD := podman
PREFIX := ovirt/vdsm-network-tests

targets := alma-9 centos-8 centos-9
types := functional integration unit

.PHONY: $(targets) $(types)

all: $(targets)

$(targets):
	for name in $(types); do \
		cd $$name; \
		$(CONTAINER_CMD) build --no-cache --rm -t $(PREFIX)-$$name:$@ -f Dockerfile.$@ . || exit $$?; \
		cd -; \
	done

$(types):
	for target in $(targets); do \
		cd $@; \
		$(CONTAINER_CMD) build --no-cache --rm -t $(PREFIX)-$@:$$target -f Dockerfile.$$target . || exit $$?; \
		cd -; \
	done
