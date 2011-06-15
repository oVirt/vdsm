#!/usr/bin/python

import hooking
from config import config

if config.getboolean('vars', 'fake_kvm_support'):
    domxml = hooking.read_domxml()

    graphics = domxml.getElementsByTagName("graphics")[0]
    graphics.removeAttribute("passwdValidTo")

    for memtag in ("memory", "currentMemory"):
        memvalue = domxml.getElementsByTagName(memtag)[0]
        while memvalue.firstChild:
            memvalue.removeChild(memvalue.firstChild)
        memvalue.appendChild(domxml.createTextNode("20480"))

    for cputag in domxml.getElementsByTagName("cpu"):
        cputag.parentNode.removeChild(cputag)

    hooking.write_domxml(domxml)
