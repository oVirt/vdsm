# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

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
    for (k, v) in tuningParams.items():
        if k not in KSM_PARAMS.keys():
            raise Exception('Invalid key in KSM parameter: %s=%s' % (k, v))
        if int(v) < 0 or int(v) >= KSM_PARAMS[k]:
            raise Exception('Invalid value in KSM parameter: %s=%s' %
                            (k, v))
        with open('/sys/kernel/mm/ksm/%s' % k, 'w') as f:
            f.write(str(v))
