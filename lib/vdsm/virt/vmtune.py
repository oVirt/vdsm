# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import itertools
import logging

from vdsm import utils
from vdsm.virt import vmxml

log = logging.getLogger('virt.vmtune')


def io_tune_values_to_dom(values, dom):
    """
    Create a DOM representation of the passed iotune values and
    attach it to the dom object in the form of nodes.

    :param values: Dictionary mapping iotune key to its value
    :param dom: XML DOM object to attach the result to
    """
    ops = ("total", "read", "write")
    units = ("bytes", "iops")

    for op, unit in itertools.product(ops, units):
        name = op + "_" + unit + "_sec"
        if name in values and values[name] >= 0:
            el = vmxml.Element(name)
            el.appendTextNode(str(values[name]))
            vmxml.append_child(dom, el)


def collect_inner_elements(el, d):
    """
    This helper method collects all nodes in el and adds them
    to dictionary d.

    :param el: XML DOM element object with text only children
    :param d: Dictionary to add the values to
    """
    for chel in vmxml.children(el):
        try:
            d[vmxml.tag(chel)] = int(vmxml.text(chel))
        except (IndexError, ValueError):
            log.exception("Invalid value for %s", vmxml.tag(chel))


def io_tune_dom_to_values(dom):
    """
    This method converts the VmDiskDeviceTuneLimits structure from its
    XML representation to the dictionary representation.

    :param dom: XML DOM object to parse
    :return: The structure in the dictionary form
    """
    values = {}

    if vmxml.attr(dom, "name"):
        values["name"] = vmxml.attr(dom, "name")

    if vmxml.attr(dom, "path"):
        values["path"] = vmxml.attr(dom, "path")

    element = vmxml.find_first(dom, "guaranteed", None)
    if element is not None:
        values["guaranteed"] = {}
        collect_inner_elements(element, values["guaranteed"])

    element = vmxml.find_first(dom, "maximum", None)
    if element is not None:
        values["maximum"] = {}
        collect_inner_elements(element, values["maximum"])

    return values


def io_tune_dom_all_to_list(dom):
    """
    This method converts all VmDiskDeviceTuneLimits structures
    in the XML to a list of dictionaries

    :param dom: XML DOM object to parse
    :return: List of VmDiskDeviceTuneLimits dictionaries
    """
    tunables = []
    for device in vmxml.find_all(dom, "device"):
        tunables.append(io_tune_dom_to_values(device))

    return tunables


def io_tune_to_dom(tune):
    """
    This method converts the VmDiskDeviceTuneLimits structure from the
    dictionary representation to the XML representation.

    :param tune: Dictionary representation of VmDiskDeviceTuneLimits
    :return: DOM XML of device node filled with values
    """
    device = vmxml.Element("device")

    if "name" in tune and tune["name"]:
        vmxml.set_attr(device, "name", tune["name"])

    if "path" in tune and tune["path"]:
        vmxml.set_attr(device, "path", tune["path"])

    if "maximum" in tune:
        maximum = vmxml.Element("maximum")
        vmxml.append_child(device, maximum)
        io_tune_values_to_dom(tune["maximum"], maximum)

    if "guaranteed" in tune:
        guaranteed = vmxml.Element("guaranteed")
        vmxml.append_child(device, guaranteed)
        io_tune_values_to_dom(tune["guaranteed"], guaranteed)

    return device


def io_tune_list_to_dom(tunables):
    """
    This method converts a list of VmDiskDeviceTuneLimits dictionaries
    to XML representation.

    :param tunables: List of VmDiskDeviceTuneLimits dictionaries
    :return: DOM XML all device nodes
    """
    io_tune = vmxml.Element("ioTune")

    for tune in tunables:
        device = io_tune_to_dom(tune)
        vmxml.append_child(io_tune, device)

    return io_tune


def io_tune_merge(old, new):
    """
    Merge two VmDiskDeviceTuneLimits structures in their dictionary form
    and return the new iotune setting.

    :param old: VmDiskDeviceTuneLimits in dict form
    :param new: VmDiskDeviceTuneLimits in dict form
    :return: old + new (in this order) in the dict form
    """

    result = utils.picklecopy(old)

    if "name" in new:
        result["name"] = new["name"]

    if "path" in new:
        result["path"] = new["path"]

    result.setdefault("maximum", {})
    if "maximum" in new:
        result["maximum"].update(new["maximum"])

    result.setdefault("guaranteed", {})
    if "guaranteed" in new:
        result["guaranteed"].update(new["guaranteed"])

    return result


def io_tune_update_list(tunables, changes):
    """
    This method updates elements in a list of VmDiskDeviceTuneLimits

    :param tunables: List of VmDiskDeviceTuneLimits to be updated
    :param changes:  List of VmDiskDeviceTuneLimits with changes
    """

    indexByPath = {}
    indexByName = {}

    for id, tune in enumerate(tunables):
        if "path" in tune:
            indexByPath[tune["path"]] = id

        if "name" in tune:
            indexByName[tune["name"]] = id

    for change in changes:
        old_id = None
        if ("name" in change and
                change["name"] in indexByName):
            old_id = indexByName[change["name"]]
        elif ("path" in change and
                change["path"] in indexByPath):
            old_id = indexByPath[change["path"]]

        if old_id is None:
            new_tune = utils.picklecopy(change)
            tunables.append(new_tune)
        else:
            old_tune = tunables[old_id]
            new_tune = io_tune_merge(old_tune, change)
            tunables[old_id] = new_tune


def _check_io_tune_categories(ioTuneParamsInfo):
    categories = ("bytes", "iops")
    for category in categories:
        if ioTuneParamsInfo.get('total_' + category + '_sec', 0) and \
                (ioTuneParamsInfo.get('read_' + category + '_sec', 0) or
                 ioTuneParamsInfo.get('write_' + category + '_sec', 0)):
            raise ValueError('A non-zero total value and non-zero'
                             ' read/write value for %s_sec can not be'
                             ' set at the same time' % category)


def validate_io_tune_params(params):
    ioTuneParams = ('total_bytes_sec', 'read_bytes_sec',
                    'write_bytes_sec', 'total_iops_sec',
                    'write_iops_sec', 'read_iops_sec')
    for key, value in params.items():
        try:
            if key in ioTuneParams:
                params[key] = int(value)
                if params[key] >= 0:
                    continue
            else:
                raise Exception('parameter %s name is invalid' % key)
        except ValueError as e:
            e.args = ('an integer is required for ioTune'
                      ' parameter %s' % key,) + e.args[1:]
            raise
        else:
            raise ValueError('parameter %s value should be'
                             ' equal or greater than zero' % key)

    _check_io_tune_categories(params)
