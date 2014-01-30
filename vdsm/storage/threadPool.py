#
# Adapted by Red Hat from
# http://code.activestate.com/recipes/203871-a-generic-programming-thread-pool/
# Author: Tim Lesher
# License: PSF License
# http://wiki.python.org/moin/PythonSoftwareFoundationLicenseV2Easy
#

import threading
from time import sleep
from Queue import Queue, Empty
import logging

# Ensure booleans exist (not needed for Python 2.2.1 or higher)
try:
    True
except NameError:
    False = 0
    True = not False


class ThreadPool:

    """Flexible thread pool class.  Creates a pool of threads, then
    accepts tasks that will be dispatched to the next available
    thread."""

    log = logging.getLogger('Storage.ThreadPool')

    def __init__(self, numThreads, waitTimeout=3, maxTasks=100):

        """Initialize the thread pool with numThreads workers."""

        self.log.debug("Enter - numThreads: %s, waitTimeout: %s, maxTasks: %s",
                       numThreads, waitTimeout, maxTasks)
        self.__threads = []
        self._taskThread = {}
        self.__resizeLock = threading.Condition(threading.Lock())
        self.__runningTasksLock = threading.Condition(threading.Lock())
        self.__tasks = Queue(maxTasks)
        self.__isJoining = False
        self.__runningTasks = 0
        self.__waitTimeout = waitTimeout
        self.setThreadCount(numThreads)

    def setRunningTask(self, addTask):

        """ Internal method to increase or decrease a counter of current
        executing tasks."""

        self.__runningTasksLock.acquire()
        try:
            if addTask:
                self.__runningTasks += 1
            else:
                self.__runningTasks -= 1
            self.log.debug("Number of running tasks: %s", self.__runningTasks)
        finally:
            self.__runningTasksLock.release()

    def getRunningTasks(self):
        return self.__runningTasks

    def setThreadCount(self, newNumThreads):

        """ External method to set the current pool size.  Acquires
        the resizing lock, then calls the internal version to do real
        work."""

        # Can't change the thread count if we're shutting down the pool!
        if self.__isJoining:
            return False

        self.__resizeLock.acquire()
        try:
            self.__setThreadCountNolock(newNumThreads)
        finally:
            self.__resizeLock.release()
        return True

    def __setThreadCountNolock(self, newNumThreads):

        """Set the current pool size, spawning or terminating threads
        if necessary.  Internal use only; assumes the resizing lock is
        held."""

        # If we need to grow the pool, do so
        while newNumThreads > len(self.__threads):
            newThread = WorkerThread(self)
            self.__threads.append(newThread)
            newThread.start()
        # If we need to shrink the pool, do so
        while newNumThreads < len(self.__threads):
            self.__threads[0].goAway()
            del self.__threads[0]

    def getThreadCount(self):

        """Return the number of threads in the pool."""

        self.__resizeLock.acquire()
        try:
            return len(self.__threads)
        finally:
            self.__resizeLock.release()

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
        except Empty:
            pass

        return id, cmd, args, callback

    def stopThread(self):
        return self.__tasks.put((None, None, None, None))

    def joinAll(self, waitForTasks=True, waitForThreads=True):

        """ Clear the task queue and terminate all pooled threads,
        optionally allowing the tasks and threads to finish."""

        # Mark the pool as joining to prevent any more task queuing
        self.__isJoining = True

        # Wait for tasks to finish
        if waitForTasks:
            while not self.__tasks.empty():
                sleep(0.1)

        # Tell all the threads to quit
        self.__resizeLock.acquire()
        try:
            # Wait until all threads have exited
            if waitForThreads:
                for t in self.__threads:
                    t.goAway()
                for t in self.__threads:
                    t.join()
#                    print t,"joined"
                    del t
            self.__setThreadCountNolock(0)
            self.__isJoining = True

            # Reset the pool for potential reuse
            self.__isJoining = False
        finally:
            self.__resizeLock.release()


class WorkerThread(threading.Thread):

    """ Pooled thread class. """

    log = logging.getLogger('Storage.ThreadPool.WorkerThread')

    def __init__(self, pool):

        """ Initialize the thread and remember the pool. """
        threading.Thread.__init__(self)
        self.__pool = pool
        self.__isDying = False
        self.daemon = True

    def _processNextTask(self):
        id, cmd, args, callback = self.__pool.getNextTask()
        try:
            if id is None:  # should retry.
                pass
            elif self.__isDying:
                # return the task into the queue, since we abort.
                self.__pool.__tasks.put((id, cmd, args, callback))
            elif callback is None:
                self.__pool.setRunningTask(True)
                self.setName(id)
                self.log.debug("Task: %s running: %s with: %s" %
                               (id, repr(cmd), repr(args)))
                cmd(args)
                self.__pool.setRunningTask(False)
            else:
                self.__pool.setRunningTask(True)
                self.setName(id)
                callback(cmd(args))
                self.__pool.setRunningTask(False)
        except Exception:
            self.log.error("Task %s failed" % repr(cmd), exc_info=True)

    def run(self):

        """ Until told to quit, retrieve the next task and execute
        it, calling the callback if any.  """

        while not self.__isDying:
            self._processNextTask()

    def goAway(self):

        """ Exit the run loop next time through."""

        self.__isDying = True


# Usage example
if __name__ == "__main__":

    from random import randrange

    # Sample task 1: given a start and end value, shuffle integers,
    # then sort them

    def sortTask(data):
        print "SortTask starting for ", data
        numbers = range(data[0], data[1])
        for a in numbers:
            rnd = randrange(0, len(numbers) - 1)
            a, numbers[rnd] = numbers[rnd], a
        print "SortTask sorting for ", data
        numbers.sort()
        print "SortTask done for ", data
        return "Sorter ", data

    # Sample task 2: just sleep for a number of seconds.

    def waitTask(data):
        print "WaitTask starting for ", data
        print "WaitTask sleeping for %d seconds" % data
        sleep(data)
        return "Waiter ", data

    # Both tasks use the same callback

    def taskCallback(data):
        print "Callback called for", data

    # Create a pool with three worker threads

    pool = ThreadPool(100)

    # Insert tasks into the queue and let them run
    print "Running tasks: ", pool.getRunningTasks(), "\n"
    pool.queueTask(sortTask, (1000, 100000), taskCallback)
    print "Running tasks: ", pool.getRunningTasks(), "\n"
    pool.queueTask(waitTask, 5, taskCallback)
    pool.queueTask(sortTask, (200, 200000), taskCallback)
    pool.queueTask(waitTask, 2, taskCallback)
    print "Running tasks: ", pool.getRunningTasks(), "\n"
    pool.queueTask(sortTask, (3, 30000), taskCallback)
    pool.queueTask(waitTask, 7, taskCallback)

    # When all tasks are finished, allow the threads to terminate
    pool.joinAll()
    print "ThreadPool sample done.\n"
