#
# Copyright 2014-2017 Red Hat, Inc.
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#
from __future__ import absolute_import

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


def create_device_index(ioTune):
    """
    Create by name / by path dictionaries from the XML representation.
    Returns a tuple (by_name, by_path) where the items are the respective
    dictionaries.

    :param dom: The root element (devices) to traverse
    :return: (by_name, by_path)
    """

    ioTuneByPath = {}
    ioTuneByName = {}

    for el in vmxml.find_all(ioTune, "device"):
        # Only one of the path and name fields is mandatory
        if vmxml.attr(el, "path"):
            ioTuneByPath[vmxml.attr(el, "path")] = el

        if vmxml.attr(el, "name"):
            ioTuneByName[vmxml.attr(el, "name")] = el

    return ioTuneByName, ioTuneByPath


def update_io_tune_dom(ioTune, tunables):
    """
    This method takes a list of VmDiskDeviceTuneLimits objects and applies
    the changes to the XML element representing the current iotune settings,

    The return value then specifies how many devices were updated.

    :param ioTune: XML object representing the ioTune metadata node
    :param tunables: list of VmDiskDeviceTuneLimits objects
    :return: number of updated devices
    """

    count = 0

    # Get all existing ioTune records and create name/path index
    ioTuneByName, ioTuneByPath = create_device_index(ioTune)

    for limit_object in tunables:
        old_tune = None
        if ("name" in limit_object and
                limit_object["name"] in ioTuneByName):
            old_tune = ioTuneByName[limit_object["name"]]
            vmxml.remove_child(ioTune, old_tune)
        elif ("path" in limit_object and
                limit_object["path"] in ioTuneByPath):
            old_tune = ioTuneByPath[limit_object["path"]]
            vmxml.remove_child(ioTune, old_tune)

        if old_tune is not None:
            old_object = io_tune_dom_to_values(old_tune)
            limit_object = io_tune_merge(old_object, limit_object)

        new_tune = io_tune_to_dom(limit_object)
        vmxml.append_child(ioTune, new_tune)
        count += 1

        # Make sure everything is OK when the same name is passed
        # twice by updating the index
        if ("name" in limit_object and
                limit_object["name"] in ioTuneByName):
            ioTuneByName[limit_object["name"]] = new_tune

        if ("path" in limit_object and
                limit_object["path"] in ioTuneByPath):
            ioTuneByPath[limit_object["path"]] = new_tune

    return count


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
    for key, value in params.iteritems():
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
