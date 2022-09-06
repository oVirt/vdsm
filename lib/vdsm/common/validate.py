# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from vdsm.common import exception


def require_keys(params, keys):
    missing = [key for key in keys if key not in params]
    if missing:
        raise exception.MissingParameter(missing=missing, params=params)


def normalize_pci_address(domain, bus, slot, function):
    """
    Various formats are legal for PCI address representation;
    see: https://wiki.xen.org/wiki/Bus:Device.Function_%28BDF%29_Notation

    To simplify the device handling code, we always reason in terms of
    hex values with proper padding. This function accepts
    the *legal* values received by libvirt and emits their normalized
    representation, as string.

    Args:
        domain: the PCI domain value, as string.
        bus: the PCI bus, as string.
        slot: the PCI slot, as string.
        function: the PCI device function, as string.

    Returns:
        Normalized dictionary representing the PCI address. Both keys and
        values will be strings.

    Example:
        normalize_pci_address('0', '4', '1', '3') ->
        {
           'domain': '0x0000',
           'bus': '0x04',
           'slot': '0x01',
           'function': '0x3'
        }
    """
    if all(v.startswith('0x') for v in (domain, bus, slot, function)):
        base = 16
    # we could also get dec values
    elif all(not v.startswith('0x') for v in (domain, bus, slot, function)):
        base = 10
    # anything else is unsupported
    else:
        raise ValueError('unsupported address format')
    return {
        'domain': '{:0=#06x}'.format(int(domain, base=base)),
        'bus': '{:0=#04x}'.format(int(bus, base=base)),
        'slot': '{:0=#04x}'.format(int(slot, base=base)),
        'function': '{:0=#02x}'.format(int(function, base=base))
    }
