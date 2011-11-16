#!/usr/bin/python

import os
import sys
import hooking
import traceback

'''
smartcard vdsm hook
adding to domain xml
<smartcard mode='passthrough' type='spicevmc'/>
'''

if os.environ.has_key('smartcard'):
    try:
        sys.stderr.write('smartcard: adding smartcard support\n')
        domxml = hooking.read_domxml()

        devices = domxml.getElementsByTagName('devices')[0]
        card = domxml.createElement('smartcard')
        card.setAttribute('mode', 'passthrough')
        card.setAttribute('type', 'spicevmc')

        devices.appendChild(card)

        hooking.write_domxml(domxml)
    except:
        sys.stderr.write('smartcard: [unexpected error]: %s\n' % traceback.format_exc())
        sys.exit(2)
