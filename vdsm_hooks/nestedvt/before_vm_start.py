#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import hooking

from vdsm import osinfo

cpu_nested_features = {
    "kvm_intel": "vmx",
    "kvm_amd": "svm",
}

nestedvt = osinfo.nested_virtualization()

if nestedvt.enabled:
    domxml = hooking.read_domxml()
    feature_vmx = domxml.createElement("feature")
    feature_vmx.setAttribute("name", cpu_nested_features[nestedvt.kvm_module])
    feature_vmx.setAttribute("policy", "require")
    domxml.getElementsByTagName("cpu")[0].appendChild(feature_vmx)
    hooking.write_domxml(domxml)
