# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

targets := alma-9 centos-9 centos-10
prefix := ovirt/vdsm-test

.PHONY: $(targets) push

all: $(targets)

$(targets):
	podman build -t $(prefix):$@ -f Dockerfile.$@ .

push:
	for name in $(targets); do \
		podman push $(prefix):$$name quay.io/$(prefix):$$name; \
	done
