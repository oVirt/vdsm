#
# Adapted by Red Hat from
# http://code.activestate.com/recipes/203871-a-generic-programming-thread-pool/
# Author: Tim Lesher
# License: PSF License
# http://wiki.python.org/moin/PythonSoftwareFoundationLicenseV2Easy
#

from __future__ import absolute_import
from __future__ import print_function

import logging
import threading

from six.moves import queue

from vdsm.common import concurrent


class ThreadPool:

    """Flexible thread pool class.  Creates a pool of threads, then
    accepts tasks that will be dispatched to the next available
    thread."""

    log = logging.getLogger('storage.ThreadPool')

    def __init__(self, name, numThreads, waitTimeout=3, maxTasks=100):

        """Initialize the thread pool with numThreads workers."""

        self.log.debug("Enter - name: %s, numThreads: %s, waitTimeout: %s, "
                       "maxTasks: %s",
                       name, numThreads, waitTimeout, maxTasks)
        self._name = name
        self.__threads = []
        self.__resizeLock = threading.Condition(threading.Lock())
        self.__runningTasksLock = threading.Condition(threading.Lock())
        self.__tasks = queue.Queue(maxTasks)
        self.__isJoining = False
        self.__runningTasks = 0
        self.__waitTimeout = waitTimeout

        for i in range(numThreads):
            name = "%s/%d" % (self._name, i)
            newThread = WorkerThread(self, name)
            newThread.start()
            self.__threads.append(newThread)

    def queueTask(self, id, task, args=None, taskCallback=None):

        """Insert a task into the queue.  task must be callable;
        args and taskCallback can be None."""

        if self.__isJoining:
            return False
        if not callable(task):
            return False

        self.__tasks.put((id, task, args, taskCallback))

        return True

    def getNextTask(self):

        """ Retrieve the next task from the task queue.  For use
        only by WorkerThread objects contained in the pool."""
        id = None
        cmd = None
        args = None
        callback = None

        try:
            id, cmd, args, callback = self.__tasks.get(True,
                                                       self.__waitTimeout)
        except queue.Empty:
            pass

        return id, cmd, args, callback

    def _task_started(self):
        """
        Called from worker threads when task is started.
        """
        with self.__runningTasksLock:
            self.__runningTasks += 1
            self.log.debug("Number of running tasks: %s", self.__runningTasks)

    def _task_finished(self):
        """
        Called from worker threads when task is finished.
        """
        with self.__runningTasksLock:
            self.__runningTasks -= 1
            self.log.debug("Number of running tasks: %s", self.__runningTasks)

    def joinAll(self, waitForThreads=True):

        """ Clear the task queue and terminate all pooled threads,
        optionally allowing the tasks and threads to finish."""

        # Mark the pool as joining to prevent any more task queuing
        self.__isJoining = True

        # Tell all the threads to quit
        self.__resizeLock.acquire()
        try:
            # Wait until all threads have exited
            if waitForThreads:
                for t in self.__threads:
                    t.goAway()
                for t in self.__threads:
                    t.join()
                del self.__threads[:]
        finally:
            self.__resizeLock.release()


class WorkerThread(object):

    """ Pooled thread class. """

    log = logging.getLogger('storage.ThreadPool.WorkerThread')

    def __init__(self, pool, name):

        """ Initialize the thread and remember the pool. """
        self._thread = concurrent.thread(self.run, name=name)
        self.__pool = pool
        self.__isDying = False

    def start(self):
        self._thread.start()

    def join(self):
        self._thread.join()

    def _processNextTask(self):
        id, cmd, args, callback = self.__pool.getNextTask()
        try:
            if id is None:  # should retry.
                pass
            elif self.__isDying:
                # return the task into the queue, since we abort.
                self.__pool.__tasks.put((id, cmd, args, callback))
            elif callback is None:
                self.__pool._task_started()
                self.log.info("START task %s (cmd=%r, args=%r)",
                              id, cmd, args)
                cmd(args)
                self.log.info("FINISH task %s", id)
                self.__pool._task_finished()
            else:
                self.__pool._task_started()
                self.log.info("START task %s (callback=%r, cmd=%r, args=%r)",
                              id, callback, cmd, args)
                callback(cmd(args))
                self.log.info("FINISH task %s", id)
                self.__pool._task_finished()
        except Exception:
            self.log.exception("FINISH task %s failed (callback=%r, "
                               "cmd=%r, args=%r)",
                               id, callback, cmd, args)

    def run(self):

        """ Until told to quit, retrieve the next task and execute
        it, calling the callback if any.  """

        while not self.__isDying:
            self._processNextTask()

    def goAway(self):

        """ Exit the run loop next time through."""

        self.__isDying = True
