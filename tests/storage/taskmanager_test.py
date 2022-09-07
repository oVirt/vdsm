# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
from __future__ import division

from contextlib import contextmanager

from vdsm.storage import outOfProcess as oop
from vdsm.storage import task
from vdsm.storage import taskManager

from . storagetestlib import Callable


WAIT_TIMEOUT = 5  # Used for task done or hang timeout


@contextmanager
def task_manager(workers=1):
    tm = taskManager.TaskManager(tpSize=workers, waitTimeout=0.05)
    try:
        yield tm
    finally:
        # Stop all managed tasks without auto recovery
        for task_id in tm.getAllTasks():
            task = tm._getTask(task_id)
            task.setRecoveryPolicy("none")
            task.stop()
        tm.prepareForShutdown(wait=True)
        tm.unloadTasks()
        oop.stop()


def test_persistent_job(tmpdir, add_recovery):
    store = str(tmpdir)
    # Simulate SPM starting a persistent job and fencing out
    with task_manager() as tm:
        # Create a task
        c = Callable(hang_timeout=WAIT_TIMEOUT)
        t = task.Task(id="task-id", abort_callback=c.finish)

        # Add recovery for task
        r = add_recovery(t, "fakerecovery", ["arg1", "arg2", "arg3"])

        # Simulate async call for a task job
        t.prepare(tm.scheduleJob, "tag", store, t, "job", c)
        c.wait_until_running()
        assert "task-id" in tm.getAllTasks()

        # Prevent storing the state to simulate SPM fencing
        # with an unexpected shutdown
        t.store = None

    # Simulate another SPM recovering the stored task
    with task_manager() as tm:
        tm.loadDumpedTasks(store)

        # Start recovery
        assert r.args is None
        tm.recoverDumpedTasks()

        # Wait for recovery to finish
        t = tm._getTask("task-id")
        assert t.wait(timeout=WAIT_TIMEOUT), "Task is not finished"

        # Check that recovery was called
        assert r.args == ("arg1", "arg2", "arg3")

        # Check that task is in a recovered state
        t.getState() == "recovered"


def test_revert_task(add_recovery):
    with task_manager() as tm:
        # Create a task
        c = Callable(hang_timeout=WAIT_TIMEOUT)
        t = task.Task(id="task-id", abort_callback=c.finish)

        # Add recovery to task
        r = add_recovery(t, "fakerecovery", ["arg1", "arg2", "arg3"])

        # Run the task
        t.prepare(tm.scheduleJob, "tag", None, t, "job", c)
        c.wait_until_running()
        assert "task-id" in tm.getAllTasks()

        # Finish the running task
        c.finish()
        assert t.wait(timeout=WAIT_TIMEOUT), "Task is not finished"

        # Revert the task and run recovery rollback
        assert r.args is None
        tm.revertTask("task-id")
        assert r.args == ("arg1", "arg2", "arg3")

        # Check that task is in a recovered state
        t.getState() == "recovered"


def test_stop_clear_task(add_recovery):
    with task_manager() as tm:
        # Create a task
        c = Callable(hang_timeout=WAIT_TIMEOUT)
        t = task.Task(id="task-id", abort_callback=c.finish)

        # Add recovery to task
        r = add_recovery(t, "fakerecovery", "arg")

        # Run the task
        t.prepare(tm.scheduleJob, "tag", None, t, "job", c)
        c.wait_until_running()
        assert "task-id" in tm.getAllTasks()

        # Abort the task
        assert r.args is None
        tm.stopTask("task-id")

        # Wait for task to finish
        assert t.wait(timeout=WAIT_TIMEOUT), "Task is not finished"
        assert c.is_finished()

        # Assert that recovery was run
        assert r.args == ("arg",)

        # Check that task is in a recovered state
        t.getState() == "recovered"

        # Clear the task from the manager list
        tm.clearTask("task-id")
        assert "task-id" not in tm.getAllTasks()
