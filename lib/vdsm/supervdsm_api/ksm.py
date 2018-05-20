# Copyright 2016 Red Hat, Inc.
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
from __future__ import absolute_import
from __future__ import division

import six

from . import expose


@expose
def ksmTune(tuningParams):
    '''
    Set KSM tuning parameters for MOM, which runs without root privilege
    when it's lauched by vdsm. So it needs supervdsm's assistance to tune
    KSM's parameters.
    '''
    KSM_PARAMS = {'run': 3, 'merge_across_nodes': 3,
                  'sleep_millisecs': 0x100000000,
                  'pages_to_scan': 0x100000000}
    for (k, v) in six.iteritems(tuningParams):
        if k not in six.iterkeys(KSM_PARAMS):
            raise Exception('Invalid key in KSM parameter: %s=%s' % (k, v))
        if int(v) < 0 or int(v) >= KSM_PARAMS[k]:
            raise Exception('Invalid value in KSM parameter: %s=%s' %
                            (k, v))
        with open('/sys/kernel/mm/ksm/%s' % k, 'w') as f:
            f.write(str(v))
