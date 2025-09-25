# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

CONTAINER_CMD := podman
PREFIX := ovirt/vdsm-network-tests

targets := alma-9 centos-9 alma-10 centos-10
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
