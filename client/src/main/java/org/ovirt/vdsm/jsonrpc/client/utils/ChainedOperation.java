package org.ovirt.vdsm.jsonrpc.client.utils;

import java.util.concurrent.ExecutionException;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.locks.Condition;
import java.util.concurrent.locks.ReentrantLock;

/**
 * Allows to chain sent operation returning object of
 * provided type.
 *
 * @param <T> Type of the result object.
 */
public final class ChainedOperation<T> implements Future<T> {
    public interface Operation<T> {
        public void call(boolean cancelled);

        public T getResult();

        public boolean isDone();

        public boolean isCancelled();
    }

    private final ReentrantLock lock;
    private final Condition condition;
    private final Operation<T> operation;
    private boolean cancelled;
    private T result;

    public ChainedOperation(Operation<T> operation) {
        this.operation = operation;
        this.lock = new ReentrantLock();
        this.condition = lock.newCondition();
        this.cancelled = false;
        this.result = null;
    }

    public void call() {
        this.operation.call(this.cancelled);
        if (this.operation.isDone()) {
            this.result = this.operation.getResult();
            try (LockWrapper wrapper = new LockWrapper(this.lock)) {
                this.condition.signalAll();
            }
        }
    }

    private void await() throws InterruptedException {
        try (LockWrapper wrapper = new LockWrapper(this.lock)) {
            this.condition.await();
        }
    }

    private void await(final long timeout, final TimeUnit unit)
            throws InterruptedException, TimeoutException {
        try (LockWrapper wrapper = new LockWrapper(this.lock)) {
            if (!condition.await(timeout, unit)) {
                throw new TimeoutException();
            }
        }
    }

    @Override
    public boolean cancel(boolean mayInterruptIfRunning) {
        this.cancelled = true;
        while (true) {
            try {
                await();
                break;
            } catch (InterruptedException e) {
                // try again
            }
        }
        return this.operation.isCancelled();
    }

    @Override
    public T get() throws InterruptedException, ExecutionException {
        await();
        return this.result;
    }

    @Override
    public T get(long timeout, TimeUnit unit) throws InterruptedException,
            ExecutionException, TimeoutException {
        await(timeout, unit);
        return this.result;
    }

    @Override
    public boolean isCancelled() {
        return this.operation.isCancelled();
    }

    @Override
    public boolean isDone() {
        return this.operation.isDone();
    }
}
