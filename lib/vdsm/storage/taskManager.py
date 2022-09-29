# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import
import os
import logging
import threading

from vdsm.config import config
from vdsm.storage import exception as se
from vdsm.storage.task import Task, Job, TaskCleanType
from vdsm.storage.threadPool import ThreadPool


class TaskManager:
    log = logging.getLogger('storage.taskmanager')

    def __init__(self,
                 tpSize=config.getint('irs', 'thread_pool_size'),
                 waitTimeout=3,
                 maxTasks=config.getint('irs', 'max_tasks')):
        self.tp = ThreadPool("tasks", tpSize, waitTimeout, maxTasks)
        self._tasks = {}
        self._unqueuedTasks = []
        self._lock = threading.Lock()

    def queue(self, task):
        return self._queueTask(task, task.commit)

    def queueRecovery(self, task):
        return self._queueTask(task, task.recover)

    def _queueTask(self, task, method):
        with self._lock:
            if task.id in self._tasks:
                raise se.AddTaskError(
                    'Task id already in use: {0}'.format(task.id))

            self.log.debug("queuing task: %s", task.id)
            self._tasks[task.id] = task

        try:
            if not self.tp.queueTask(task.id, method):
                self.log.error("unable to queue task: %s", task.dumpTask())
                with self._lock:
                    del self._tasks[task.id]
                raise se.AddTaskError()
            self.log.debug("task queued: %s", task.id)
        except Exception:
            self.log.exception('could not queue task %s', task.id)
            raise

        return task.id

    def scheduleJob(self, type, store, task, jobName, func, *args):
        task.setTag(type)
        if store is not None:
            task.setPersistence(store, cleanPolicy=TaskCleanType.manual)
        task.setManager(self)
        task.setRecoveryPolicy("auto")
        task.addJob(Job(jobName, func, *args))
        self.log.debug("scheduled job %s for task %s ", jobName, task.id)

    def _getTask(self, taskID):
        Task.validateID(taskID)
        t = self._tasks.get(taskID, None)
        if t is None:
            raise se.UnknownTask(taskID)
        return t

    def prepareForShutdown(self, wait=False):
        """ Prepare to shutdown and stop all threads.
        """
        self.log.debug("Request to stop all threads (wait=%s)", wait)
        self.tp.joinAll(waitForThreads=wait)

    def getTaskStatus(self, taskID):
        """ Internal return Task status for a given task.
        """
        self.log.debug("Entry. taskID: %s", taskID)
        t = self._getTask(taskID)
        status = t.deprecated_getStatus()
        self.log.debug("Return. Response: %s", status)
        return status

    def getAllTasksStatuses(self, tag=None):
        """ Return Task status for all tasks by type.
        """
        self.log.debug("Entry.")
        subRes = {}
        with self._lock:
            for taskID, task in self._tasks.items():
                if not tag or tag in task.getTags():
                    try:
                        subRes[taskID] = task.deprecated_getStatus()
                    except se.UnknownTask:
                        # Return statuses for existing tasks only.
                        self.log.warn("Unknown task %s. "
                                      "Maybe task was already cleared.",
                                      taskID)
        self.log.debug("Return: %s", subRes)
        return subRes

    def getAllTasks(self):
        """
        Return Tasks for all public tasks.
        """
        self.log.debug("Entry.")
        subRes = {}
        with self._lock:
            for taskID, task in self._tasks.items():
                try:
                    subRes[taskID] = task.getDetails()
                except se.UnknownTask:
                    # Return info for existing tasks only.
                    self.log.warn("Unknown task %s. Maybe task was already "
                                  "cleared.", taskID)
        self.log.debug("Return: %s", subRes)
        return subRes

    def unloadTasks(self, tag=None):
        """
        Remove Tasks from managed tasks list
        """
        self.log.debug("Entry.")
        with self._lock:
            for taskID, task in list(self._tasks.items()):
                if not tag or tag in task.getTags():
                    self._tasks.pop(taskID, None)
        self.log.debug("Return")

    def stopTask(self, taskID, force=False):
        """ Stop a task according to given uuid.
        """
        self.log.debug("Entry. taskID: %s", taskID)
        t = self._getTask(taskID)
        t.stop(force=force)
        self.log.debug("Return.")
        return True

    def revertTask(self, taskID):
        self.log.debug("Entry. taskID: %s", taskID)
        # TODO: Should we stop here implicitly ???
        t = self._getTask(taskID)
        t.rollback()
        self.log.debug("Return.")

    def clearTask(self, taskID):
        """ Clear a task according to given uuid.
        """
        self.log.debug("Entry. taskID: %s", taskID)
        # TODO: Should we stop here implicitly ???
        t = self._getTask(taskID)

        # Task clean procedure may block so we don't want to do it under tasks
        # dict lock, instead use pop under lock in case the task was already
        # removed by another call.
        t.clean()
        with self._lock:
            self._tasks.pop(taskID, None)

        self.log.debug("Return.")

    def getTaskInfo(self, taskID):
        """ Return task's data according to given uuid.
        """
        self.log.debug("Entry. taskID: %s", taskID)
        t = self._getTask(taskID)
        info = t.getInfo()
        self.log.debug("Return. Response: %s", info)
        return info

    def getAllTasksInfo(self, tag=None):
        """ Return Task info for all public tasks.
            i.e - not internal.
        """
        self.log.debug("Entry.")
        subRes = {}
        with self._lock:
            for taskID, task in self._tasks.items():
                if not tag or tag in task.getTags():
                    try:
                        subRes[taskID] = task.getInfo()
                    except se.UnknownTask:
                        # Return info for existing tasks only.
                        self.log.warn("Unknown task %s. "
                                      "Maybe task was already cleared.",
                                      taskID)
        self.log.debug("Return. Response: %s", subRes)
        return subRes

    def loadDumpedTasks(self, store):
        if not os.path.exists(store):
            self.log.debug("task dump path %s does not exist.", store)
            return
        # taskID is the root part of each (root.ext) entry in the dump task dir
        tasksIDs = set(os.path.splitext(tid)[0] for tid in os.listdir(store))
        for taskID in tasksIDs:
            self.log.debug("Loading dumped task %s", taskID)
            try:
                t = Task.loadTask(store, taskID)
                t.setPersistence(store,
                                 str(t.persistPolicy),
                                 str(t.cleanPolicy))
                self._unqueuedTasks.append(t)
            except Exception:
                self.log.error("taskManager: Skipping directory: %s",
                               taskID,
                               exc_info=True)
                continue

    def recoverDumpedTasks(self):
        for task in self._unqueuedTasks[:]:
            self.queueRecovery(task)
            self._unqueuedTasks.remove(task)
