#
# Copyright 2019 Red Hat, Inc.
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
from __future__ import division

from contextlib import contextmanager

from vdsm.common import concurrent
from vdsm.storage.task import Job, Task, TaskCleanType

from . storagetestlib import Callable


class TaskManager(object):

    def __init__(self):
        self._task = None

    def queue(self, task):
        self._task = task

    def scheduleJob(self, task, func, store=None):
        # This is an adaptation of vdsm.storage.taskManager
        # scheduleJob() method.
        task.setTag("tag")
        if store is not None:
            task.setPersistence(store, cleanPolicy=TaskCleanType.manual)
        task.setManager(self)
        task.setRecoveryPolicy("auto")
        task.addJob(Job("job", func))


@contextmanager
def async_task(called, task_id):
    task = Task(id=task_id)
    t = concurrent.thread(task.prepare, args=(called,))
    t.start()
    try:
        called.wait_until_running()
        yield task
    finally:
        called.finish()
        t.join(timeout=1)


def test_task_prepare():
    c = Callable()
    t = Task(id="task-id")
    t.prepare(c)

    status = t.getStatus()
    assert status == {
        "result": {},
        "state": {"code": 0, "message": "OK"},
        "task": {"id": "task-id", "state": "finished"}
    }
    assert c.is_finished()


def test_task_abort():
    # Run async task
    c = Callable(hang=True)
    with async_task(c, "task-id") as t:
        # Check it is preparing
        status = t.getStatus()
        assert status == {
            "result": "",
            "state": {"code": 0, "message": "Task is initializing"},
            "task": {"id": "task-id", "state": "preparing"}
        }

        # Stop task and wait for a done state
        assert not c.is_finished()
        with t.abort_callback(c.finish):
            t.stop()

        assert t.wait(timeout=1), "Task is not stopped"

        # Check task's final status
        assert c.is_finished()
        status = t.getStatus()
        assert status == {
            "result": "",
            "state": {
                "code": 0,
                "message": ("Task prepare failed: Task is aborted: "
                            "'Unknown error encountered' - code 411")
            },
            "task": {"id": "task-id", "state": "failed"}
        }


def test_task_queued():
    t = Task(id="task-id")
    tm = TaskManager()
    c = Callable()

    # Simulate async storage APIs scheduling another function with the
    # task manager.
    def async_call():
        tm.scheduleJob(t, c)

    # Schedule job
    t.prepare(async_call)

    # Check that task is queued
    assert tm._task is t
    status = t.getStatus()
    assert status == {
        "result": "",
        "state": {"code": 0, "message": "Task is initializing"},
        "task": {"id": "task-id", "state": "queued"}
    }

    # Check that job callable was not called yet
    assert not c.is_finished()

    # Invoke the job run
    t.commit()

    # Check task final status
    status = t.getStatus()
    assert status == {
        "result": {},
        "state": {"code": 0, "message": "1 jobs completed successfully"},
        "task": {"id": "task-id", "state": "finished"}
    }

    # Check that job callable was called
    assert c.is_finished()
