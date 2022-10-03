# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

targets := centos-8 centos-9
prefix := vdsm-test

.PHONY: $(targets) push

all: $(targets)

$(targets):
	podman build -t $(prefix)-$@ -f Dockerfile.$@ .

push:
	for name in $(targets); do \
		podman push $(prefix)-$$name quay.io/ovirt/$(prefix)-$$name; \
	done
