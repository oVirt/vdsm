# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import logging

from collections import namedtuple

from decorator import decorator

from vdsm.common.threadlocal import vars

from . import exception
from . import logutils
from . import response


_log = logging.getLogger("api")


Context = namedtuple('Context', "flow_id, client_host, client_port")


def logged(on=""):
    @decorator
    def method(func, *args, **kwargs):
        log = logging.getLogger(on)
        ctx = context_string(args[0])
        log.info('START %s %s', logutils.call2str(func, args, kwargs), ctx)
        try:
            ret = func(*args, **kwargs)
        except Exception as exc:
            log.info("FINISH %s error=%s %s", func.__name__, exc, ctx)
            raise
        log.info('FINISH %s return=%s %s', func.__name__, ret, ctx)
        return ret
    return method


def context_string(api_object):
    items = []

    # Internal threads never set vars.context, so we will not have a context
    # attribute. RPC threads set context before calling the api, and set
    # context to None after that.
    ctx = getattr(vars, "context", None)
    if not ctx:
        items.append(("from", "internal"))
    else:
        items.append(("from", "%s,%s" % (ctx.client_host, ctx.client_port)))
        if ctx.flow_id:
            items.append(("flow_id", ctx.flow_id))

    # Task exists only for storage verbs
    task = getattr(vars, "task", None)
    if task:
        items.append(("task_id", task.id))

    attributes = getattr(api_object, 'LOG_ATTRIBUTES', {})
    if attributes:
        items.extend([(name, getattr(api_object, attr, '') or '',)
                      for name, attr in attributes.items()])

    return ", ".join(k + "=" + v for k, v in items)


def guard(*guarding_functions):
    """
    Decorator for methods that can be called only under certain conditions.

    Before the method is called, guarding_functions are invoked in their order
    with the same arguments as the method.  They can check for validity of the
    call and raise an exception if the call shouldn't be permitted.

    :param guarding_functions: functions to call with the decorated method
      arguments before the decorated method is actually called
    """
    @decorator
    def method(func, *args, **kwargs):
        for f in guarding_functions:
            f(*args, **kwargs)
        return func(*args, **kwargs)
    return method


@decorator
def method(func, *args, **kwargs):
    """
    Decorate an instance method, and return a response according to the
    outcome of the call.

    If the method returns None, return a plain success response.
    If the method wants to augment the success response, it could return
    a dict. The dict items will be added to the success response.
    The method could override the success response message this way.

    If the method raises a VdsmException or one subclass, the decorator
    will produce the corresponding error response.
    If the method raises any other exception, the decorator will produce a
    general exception response with the details of the original error.
    """

    _log.debug("START %s args=%s kwargs=%s", func.__name__, args, kwargs)
    try:
        ret = func(*args, **kwargs)
    except exception.VdsmException as e:
        if e.expected:
            # This is expected condition, typically a user error, not an error
            # of vdsm.
            _log.info("FINISH %s error=%s", func.__name__, e)
        else:
            _log.exception("FINISH %s error=%s", func.__name__, e)
        return e.response()
    except Exception as e:
        _log.exception("FINISH %s error=%s", func.__name__, e)
        e = exception.GeneralException(str(e))
        return e.response()
    _log.debug("FINISH %s response=%s", func.__name__, ret)

    # FIXME: this is temporary to allow gradual upgrade of VM API methods.
    if response.is_valid(ret):
        return ret
    if ret is None:
        return response.success()
    return response.success(**ret)
