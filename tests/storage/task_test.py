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
from vdsm.storage import outOfProcess as oop
from vdsm.storage.task import Job, Recovery, Task, TaskCleanType,\
    TaskPersistType, TaskRecoveryType

from . storagetestlib import Callable


WAIT_TIMEOUT = 5  # Used for task done or hang timeout


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
        oop.stop()


def test_task_prepare():
    result = object()
    c = Callable(result=result)
    t = Task(id="task-id")
    t.prepare(c)

    status = t.getStatus()
    assert status == {
        "result": result,
        "state": {"code": 0, "message": "OK"},
        "task": {"id": "task-id", "state": "finished"}
    }
    assert c.is_finished()


def test_task_abort():
    # Run async task
    c = Callable(hang_timeout=WAIT_TIMEOUT)
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

        assert t.wait(timeout=WAIT_TIMEOUT), "Task is not stopped"

        # Check task's final status
        assert c.is_finished()
        status = t.getStatus()
        assert status == {
            "result": "",
            "state": {
                "code": 0,
                "message": ("Task prepare failed: Task is aborted: "
                            "'value=Unknown error encountered "
                            "abortedcode=411'")
            },
            "task": {"id": "task-id", "state": "failed"}
        }


def test_task_queued():
    t = Task(id="task-id")
    tm = TaskManager()
    result = object()
    c = Callable(result=result)

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
        "result": result,
        "state": {"code": 0, "message": "1 jobs completed successfully"},
        "task": {"id": "task-id", "state": "finished"}
    }

    # Check that job callable was called
    assert c.is_finished()


def test_task_rollback(add_recovery):
    # Run async task
    c = Callable(hang_timeout=WAIT_TIMEOUT)
    with async_task(c, "task-id") as t:
        # Set automatic recovery
        t.setRecoveryPolicy("auto")

        # Add recoveries to task
        r1 = add_recovery(t, "fakerecovery1", ["arg1", "arg2"])
        r2 = add_recovery(t, "fakerecovery2", "arg")

        assert r1.args is None
        assert r2.args is None

        # Test abort flow
        with t.abort_callback(c.finish):
            t.stop()

        assert t.wait(timeout=WAIT_TIMEOUT), "Task failed to stop"

        # Check that recovery procedures were done
        assert r1.args == ("arg1", "arg2")
        assert r2.args == ("arg",)

        # Check final task status
        status = t.getStatus()
        assert status == {
            "result": "",
            "state": {
                "code": 0,
                "message": ("Task prepare failed: Task is aborted: "
                            "'value=Unknown error encountered "
                            "abortedcode=411'")
            },
            "task": {"id": "task-id", "state": "recovered"}
        }


def test_task_save_load(tmpdir, add_recovery):
    # Run async task
    c = Callable(hang_timeout=WAIT_TIMEOUT)
    with async_task(c, "task-id") as orig_task:
        orig_task.setRecoveryPolicy("auto")

        # Set persistency for task
        store = str(tmpdir)
        orig_task.setPersistence(store)

        # Add recovery to task
        r = add_recovery(orig_task, "fakerecovery", ["arg1", "arg2"])

        # Simulate original task being interrupted by improper shutdown
        orig_task.store = None

    # Load task from storage
    loaded_task = Task.loadTask(store, "task-id")

    # Recover loaded task
    assert r.args is None
    loaded_task.recover()

    # Wait for recovery to finish
    assert loaded_task.wait(timeout=WAIT_TIMEOUT), "Task failed to finish"

    # Assert recovery procedure was executed
    assert r.args == ("arg1", "arg2")

    # Check task final status
    status = loaded_task.getStatus()
    # TODO: Figure why task message still indicates initialization
    assert status == {
        "result": "",
        "state": {"code": 0, "message": "Task is initializing"},
        "task": {"id": "task-id", "state": "recovered"}
    }


def test_recovery_list():
    # Check push pop single recovery
    t = Task(id="task-id")
    recovery1 = Recovery(
        "name_1",
        "storage_1",
        "task_1",
        "func_1",
        []
    )
    t.pushRecovery(recovery1)
    assert t.popRecovery() is recovery1

    # Check replace recovery by another
    t.pushRecovery(recovery1)
    recovery2 = Recovery(
        "name_2",
        "storage_2",
        "task_2",
        "func_2",
        []
    )
    t.replaceRecoveries(recovery2)
    assert t.popRecovery() is recovery2
    assert t.popRecovery() is None

    # Check replace recovery over an empty list
    t.replaceRecoveries(recovery1)
    assert t.popRecovery() is recovery1

    # Check clearing recoveries
    t.pushRecovery(recovery1)
    t.pushRecovery(recovery2)
    t.clearRecoveries()
    assert t.popRecovery() is None


def test_enum_type():
    # Same enums, same values.
    assert TaskPersistType(TaskPersistType.none) ==\
        TaskPersistType(TaskPersistType.none)
    assert TaskPersistType(TaskPersistType.none) ==\
        TaskPersistType.none

    # Same enums, different values.
    assert TaskPersistType(TaskPersistType.manual) !=\
        TaskPersistType(TaskPersistType.auto)
    assert TaskPersistType(TaskPersistType.manual) !=\
        TaskPersistType.auto

    # Different enums, different values.
    assert TaskPersistType(TaskPersistType.none) !=\
        TaskCleanType(TaskCleanType.manual)
    assert TaskPersistType(TaskPersistType.none) !=\
        TaskCleanType.manual

    # Different enums, same values.
    assert TaskPersistType(TaskPersistType.auto) !=\
        TaskRecoveryType(TaskRecoveryType.auto)
    assert TaskPersistType(TaskPersistType.auto) ==\
        TaskRecoveryType.auto
