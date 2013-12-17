package org.ovirt.vdsm.jsonrpc.client.utils.retry;

import java.util.concurrent.TimeUnit;

/**
 * <code>RetryConext</code> represents current execution retry
 * state. Initially context is populated form the <code>RetryPolicy</code>
 * provided for the execution.
 *
 */
public class RetryContext {
    private int numberOfAttempts;
    private int timeout;
    private RetryPolicy policy;

    public RetryContext(RetryPolicy policy) {
        this.numberOfAttempts = policy.getRetryNumber();
        this.timeout = policy.getRetryTimeOut();
        this.policy = policy;
    }

    public boolean isExceptionRetryable(Exception e) {
        for (Class<? extends Exception> clazz : this.policy.getExceptions()) {
            if (clazz.isInstance(e)) {
                return true;
            }
        }
        return false;
    }

    public int getNumberOfAttempts() {
        return this.numberOfAttempts;
    }

    public void waitOperation() throws InterruptedException {
        this.policy.getTimeUnit().sleep(timeout);
    }

    public int getTimeout() {
        return this.timeout;
    }

    public TimeUnit getTimeUnit() {
        return this.policy.getTimeUnit();
    }

    public void decreaseAttempts() {
        this.numberOfAttempts--;
    }
}
