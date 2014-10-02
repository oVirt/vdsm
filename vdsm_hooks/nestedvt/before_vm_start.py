#!/usr/bin/python
#
# Copyright 2012 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import hooking

cpu_nested_features = {
    "kvm_intel": "vmx",
    "kvm_amd": "svm",
}

for kvm_mod in ("kvm_intel", "kvm_amd"):
    kvm_mod_path = "/sys/module/%s/parameters/nested" % kvm_mod
    try:
        with open(kvm_mod_path) as f:
            if f.readline().strip() in ("Y", "1"):
                break
    except IOError:
        pass
else:
    kvm_mod = None

if kvm_mod:
    domxml = hooking.read_domxml()
    feature_vmx = domxml.createElement("feature")
    feature_vmx.setAttribute("name", cpu_nested_features[kvm_mod])
    feature_vmx.setAttribute("policy", "require")
    domxml.getElementsByTagName("cpu")[0].appendChild(feature_vmx)
    hooking.write_domxml(domxml)
