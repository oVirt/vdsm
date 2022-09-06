# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

import inspect
import json
import logging

from vdsm.common import api


class SchemaInconsistencyFormatter(logging.Formatter):

    def format(self, record):
        msg = super(SchemaInconsistencyFormatter, self).format(record)
        if record.levelno == logging.DEBUG:
            return self._add_debug_info(msg)
        return msg

    def _add_debug_info(self, msg):
        rep_id = self._find_rep_id()
        relevant_frames = self._collect_relevant_frames()
        return self._format_debug_msg(rep_id, relevant_frames, msg)

    @staticmethod
    def _format_debug_msg(rep_id, relevant_frames, msg):
        header = u"{}".format(rep_id)
        message = u"With message: {}".format(msg).rstrip()
        ctx_string = u"With context: {}".format(api.context_string(None))
        if len(relevant_frames) > 0:
            backtrace_json = json.dumps(relevant_frames, indent=2,
                                        separators=(",", ":"))
            backtrace_dump = backtrace_json.replace("  ", "\t")
            backtrace = u"With backtrace: {}".format(backtrace_dump)
        else:
            backtrace = ""
        return "\n".join((header, ctx_string, message, backtrace))

    @staticmethod
    def _find_rep_id():
        for entry in inspect.stack():
            frame = entry[0]
            _, _, _, localz = inspect.getargvalues(frame)
            if "rep" in localz:
                rep = localz["rep"]
                rep_id = getattr(rep, "id", None)
                if rep_id is not None:
                    return rep_id
        return "<unknown>"

    @staticmethod
    def _collect_relevant_frames():
        SIF = SchemaInconsistencyFormatter
        frame_visitors = {
            "_check_primitive_type": SIF._check_primitive_type_visitor,
            "_verify_complex_type": SIF._verify_complex_type_visitor,
            "_verify_object_type": SIF._verify_object_type_visitor
        }
        frames = []
        for entry in inspect.stack():
            frame = entry[0]
            fn_name = inspect.getframeinfo(frame)[2]
            if fn_name in frame_visitors:
                _, _, _, localz = inspect.getargvalues(frame)
                visitor = frame_visitors[fn_name]
                info = visitor(localz)
                frames.append((fn_name, info))
        return frames

    @staticmethod
    def _verify_object_type_visitor(localz):
        default = "<unknown>"
        schema_type_name = default
        schema_type = localz.get("t")
        if schema_type is not None:
            schema_type_name = schema_type.get("name", default)
        call_arg_keys = list(localz.get("arg", {}).keys())
        return {
            "schema_type_name": schema_type_name,
            "call_arg_keys": call_arg_keys
        }

    @staticmethod
    def _check_primitive_type_visitor(localz):
        return {
            "schema_type_type": localz.get("t", "<unknown>")
        }

    @staticmethod
    def _verify_complex_type_visitor(localz):
        return {
            "schema_type_type": localz.get("t_type", "<unknown>")
        }
