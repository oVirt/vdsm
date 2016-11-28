package org.ovirt.vdsm.jsonrpc.client.utils;

import java.util.Queue;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.Future;
import java.util.concurrent.FutureTask;

/**
 * Utility class used for processing <code>FutureTask</code>s.
 *
 */
public final class ReactorScheduler {
    private volatile Queue<Future<?>> pendingOperations;

    public ReactorScheduler() {
        this.pendingOperations = new ConcurrentLinkedQueue<>();
    }

    public void queueFuture(Future<?> op) {
        this.pendingOperations.add(op);
    }

    public void performPendingOperations() {
        boolean remove = false;
        Queue<Future<?>> operations = pendingOperations;
        pendingOperations = new ConcurrentLinkedQueue<>();
        for (int i = 0; i < operations.size(); i++) {
            Future<?> task = operations.peek();
            if (task instanceof FutureTask) {
                ((FutureTask<?>) task).run();
                remove = true;
            } else {
                ChainedOperation<?> co = (ChainedOperation<?>) task;
                co.call();
                if (co.isDone()) {
                    remove = true;
                }
            }

            if (remove) {
                operations.remove(task);
                i--;
            }
        }
    }
}
