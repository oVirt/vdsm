# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from xml.etree import ElementTree as etree
import base64
import libvirt
import logging
import uuid

from vdsm.common import libvirtconnection
from vdsm.common import response
from vdsm.common import xmlutils


def register(secrets, clear=False):
    try:
        secrets = [Secret(params) for params in secrets]
    except ValueError as e:
        logging.warning("Attempt to register invalid secret: %s", e)
        return response.error("secretBadRequestErr")

    con = libvirtconnection.get()
    try:
        for secret in secrets:
            logging.info("Registering secret %s", secret)
            secret.register(con)
        if clear:
            uuids = frozenset(sec.uuid for sec in secrets)
            for virsecret in con.listAllSecrets():
                if (virsecret.UUIDString() not in uuids and
                        _is_ovirt_secret(virsecret)):
                    virsecret.undefine()
    except libvirt.libvirtError as e:
        logging.error("Could not register secret %s: %s", secret, e)
        return response.error("secretRegisterErr")

    return response.success()


def unregister(uuids):
    try:
        uuids = [str(uuid.UUID(s)) for s in uuids]
    except ValueError as e:
        logging.warning("Attempt to unregister invalid uuid %s: %s" %
                        (uuids, e))
        return response.error("secretBadRequestErr")

    con = libvirtconnection.get()
    try:
        for sec_uuid in uuids:
            logging.info("Unregistering secret %r", sec_uuid)
            try:
                virsecret = con.secretLookupByUUIDString(sec_uuid)
            except libvirt.libvirtError as e:
                if e.get_error_code() != libvirt.VIR_ERR_NO_SECRET:
                    raise
                logging.debug("No such secret %r", sec_uuid)
            else:
                virsecret.undefine()
    except libvirt.libvirtError as e:
        logging.error("Could not unregister secrets: %s", e)
        return response.error("secretUnregisterErr")

    return response.success()


def clear():
    """
    Clear all regsistered ovirt secrets.

    Should be called during startup and shutdown to ensure that we don't leave
    around stale or unneeded secrets.
    """
    logging.info("Unregistering all secrets")
    con = libvirtconnection.get()
    for virsecret in con.listAllSecrets():
        try:
            if _is_ovirt_secret(virsecret):
                virsecret.undefine()
        except libvirt.libvirtError as e:
            logging.error("Could not unregister %s: %s", virsecret, e)


def _is_ovirt_secret(virsecret):
    return virsecret.usageID().startswith("ovirt/")


class Secret(object):
    """
    Validate libvirt secret parameters and create secret xml string.

    Raises ValueError if params dictionary does not contain the required valid
    secret parameters.
    """

    _USAGE_TYPES = {"ceph": "name", "volume": "volume", "iscsi": "target"}

    def __init__(self, params):
        self.uuid = str(uuid.UUID(_get_required(params, "uuid")))
        self.usage_type = _get_enum(params, "usageType", self._USAGE_TYPES)
        self.usage_id = _get_required(params, "usageID")
        self.password = _decode_password(_get_required(params, "password"))
        self.description = params.get("description")

    def register(self, con):
        # This is racy, but we don't have a better way. This is unlikely to
        # fail, as we own libvirt and its secrets, and we do not modify the
        # same secrets concurrently.
        try:
            virsecret = con.secretLookupByUUIDString(self.uuid)
        except libvirt.libvirtError as e:
            if e.get_error_code() != libvirt.VIR_ERR_NO_SECRET:
                raise
        else:
            if virsecret.usageID() != self.usage_id:
                virsecret.undefine()

        virsecret = con.secretDefineXML(self.toxml())
        virsecret.setValue(self.password.value)

    def toxml(self):
        secret = etree.Element("secret", ephemeral="yes", private="yes")
        if self.description:
            description = etree.Element("description")
            description.text = self.description
            secret.append(description)
        uuid = etree.Element("uuid")
        uuid.text = self.uuid
        secret.append(uuid)
        usage = etree.Element("usage", type=self.usage_type)
        usage_type = etree.Element(self._USAGE_TYPES[self.usage_type])
        usage_type.text = self.usage_id
        usage.append(usage_type)
        secret.append(usage)
        return xmlutils.tostring(secret)

    def __str__(self):
        return ("Secret(uuid={self.uuid}, "
                "usage_type={self.usage_type}, "
                "usage_id={self.usage_id}, "
                "description={self.description})").format(self=self)


# TODO: Move following helpers to reusable validation module


def _decode_password(password):
    try:
        password.value = base64.b64decode(password.value)
    except TypeError as e:
        # Note: encoded value is intentionally not displayed
        raise ValueError("Unable to decode base64 password: %s" % e)
    return password


def _get_enum(params, name, values):
    value = _get_required(params, name)
    if value not in values:
        raise ValueError("Invalid value %r for %r, expecting one of %s" %
                         (value, name, values))
    return value


def _get_required(params, name):
    if name not in params:
        raise ValueError("Missing required property %r" % name)
    return params[name]
