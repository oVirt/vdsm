# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division
import functools
import logging
import os


LOGGER_NAME = "vdsm-tool"


def setup_logging(log_file, verbosity, append):
    level = logging.DEBUG if verbosity else logging.INFO
    root_logger = logging.getLogger(LOGGER_NAME)
    root_logger.setLevel(level)

    if root_logger.handlers:
        return root_logger

    if log_file is not None:
        mode = append and 'a' or 'w'
        file_handler = logging.FileHandler(log_file, mode=mode)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)-7s] %(message)s",
                "%m/%d/%Y %I:%M:%S %p"))
        # File logging always at DEBUG level
        file_handler.setLevel(logging.DEBUG)
        root_logger.addHandler(file_handler)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)-7s | %(message)s"))
    root_logger.addHandler(handler)

    return root_logger


class expose(object):
    def __init__(self, name):
        self.name = name

    def __call__(self, fun):
        fun._vdsm_tool = {"name": self.name}
        return fun


def requiresRoot(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if os.geteuid() != 0:
            raise NotRootError()
        func(*args, **kwargs)
    return wrapper


class UsageError(RuntimeError):
    """ Raise on runtime when usage is invalid """


class NotRootError(UsageError):
    def __init__(self):
        super(NotRootError, self).__init__("Must run as root")


class ExtraArgsError(UsageError):
    def __init__(self, n=0):
        if n == 0:
            message = "Command does not take extra arguments"
        else:
            message = \
                "Command takes exactly %d argument%s" % (n,
                                                         's' if n != 1 else '')
        super(ExtraArgsError, self).__init__(message)
