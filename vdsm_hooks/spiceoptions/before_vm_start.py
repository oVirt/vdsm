#!/usr/bin/python3

# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import print_function
import ast
import os
import sys
import traceback
from xml.dom import minidom

import hooking


'''
Hook to configure spice options on a vm

Syntax:
   spiceoptions={'element': {'attribute': 'value'},..

Example:
   spiceoptions={'image': {'compression': 'auto_glz'},
                 'jpeg': {'compression': 'never'},
                 'streaming':{'mode':'filter'}}


  <graphics type='spice' port='-1' tlsPort='-1' autoport='yes'>
     ******
    <image compression='auto_glz'/>
    <streaming mode='filter'/>
    <mouse mode='client'/>
  </graphics>
'''


_IMAGE_COMP = frozenset(('auto_glz', 'auto_lz', 'quic', 'glz', 'lz', 'off'))
_JPEG_COMP = frozenset(('auto', 'never', 'always'))
_ZLIB_COMP = frozenset(('auto', 'never', 'always'))
_PLAYB_COMP = frozenset(('on', 'off'))
_STR_MODE = frozenset(('filter', 'all', 'off'))
_MOUSE_MODE = frozenset(('server', 'client'))

spiceOpts = {'image': {'compression': _IMAGE_COMP},
             'jpeg': {'compression': _JPEG_COMP},
             'zlib': {'compression': _ZLIB_COMP},
             'playback': {'compression': _PLAYB_COMP},
             'streaming': {'mode': _STR_MODE},
             'mouse': {'mode': _MOUSE_MODE}}


def createElement(domxml, element, attribute, attributeValue):
    xmlElement = domxml.createElement(element)
    xmlElement.setAttribute(attribute, attributeValue)

    return xmlElement


def main():
    if 'spiceoptions' in os.environ:
        try:
            spiceConfig = ast.literal_eval(os.environ['spiceoptions'])
            spiceConfig = dict((k.lower(), v)
                               for k, v in spiceConfig.iteritems())

            domxml = hooking.read_domxml()
            for graphDev in domxml.getElementsByTagName('graphics'):
                if graphDev.getAttribute('type') == 'spice':
                    for elmt, value in spiceConfig.items():
                        if elmt not in spiceOpts:
                            sys.stderr.write(" Invalid ELEMENT"
                                             " [%s] " % elmt)
                        else:
                            for attr, attrValue in value.items():
                                if attr not in spiceOpts[elmt]:
                                    sys.stderr.write(" Invalid ATTRIBUTE"
                                                     " [%s]" % attr)
                                elif attrValue not in spiceOpts[elmt][attr]:
                                    sys.stderr.write(" Invalid VALUE"
                                                     " [%s]" % attrValue)
                                else:
                                    returnElmt = createElement(domxml,
                                                               elmt,
                                                               attr,
                                                               attrValue)
                                    if returnElmt:
                                        graphDev.appendChild(returnElmt)

            hooking.write_domxml(domxml)

        except:
            hooking.exit_hook('spiceoptions: [unexpected error]: %s\n'
                              % traceback.format_exc())
            sys.exit(2)


def test():
    text = '''<graphics type='spice' port='-1' tlsPort='-1' autoport='yes'>
       <channel name='main' mode='secure'/>
       <channel name='record' mode='insecure'/>
       <streaming mode='filter'/>
       <mouse mode='client'/>
        </graphics>'''

    xmldom = minidom.parseString(text)
    graphics = xmldom.getElementsByTagName('graphics')[0]
    print("\n Graphic device definition before execution \n %s"
          % graphics.toxml(encoding='UTF-8'))
    returnEle = createElement(xmldom, 'image', 'compression', 'glz')
    if returnEle:
        graphics.appendChild(returnEle)
    print("\n Graphic device after setting image element \n %s"
          % graphics.toxml(encoding='UTF-8'))


if __name__ == '__main__':
    try:
        if '--test' in sys.argv:
            test()
        else:
            main()
    except:
        hooking.exit_hook('spiceoptions: [unexpected error]: %s\n'
                          % traceback.format_exc())
        sys.exit(2)
