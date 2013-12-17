package org.ovirt.vdsm.jsonrpc.client.utils.retry;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.TimeUnit;

/**
 * Immutable java bean which provide information how retry logic should work.
 *
 */
public class RetryPolicy {
    private final int retryTimeOut;
    private final int retryNumber;
    private List<Class<? extends Exception>> exceptions;
    private TimeUnit timeUnit = TimeUnit.MILLISECONDS;

    /**
     * Create policy using provided values.
     * @param retryTimeOut - <code>Integer</code> value which is used as timeout between operation retry
     *                       combined with <code>TimeUnit</code> which is set to milliseconds by default.
     * @param retryNumber - <code>Integer</code> value which defines number of retry attempts.
     * @param retryableExceptions - <code>List</code> of retryable exceptions.
     */
    public RetryPolicy(int retryTimeOut, int retryNumber, List<Class<? extends Exception>> retryableExceptions) {
        this.retryNumber = retryNumber;
        this.retryTimeOut = retryTimeOut;
        this.exceptions = Collections.unmodifiableList(retryableExceptions);
    }

    public RetryPolicy(int retryTimeOut, int retryNumber) {
        this(retryTimeOut, retryNumber, new ArrayList<Class<? extends Exception>>());
    }

    public RetryPolicy(int retryTimeOut, int retryNumber, Class<? extends Exception> retryableException) {
        this(retryTimeOut, retryNumber, new ArrayList<Class<? extends Exception>>(Arrays.asList(retryableException)));
    }

    public int getRetryTimeOut() {
        return retryTimeOut;
    }

    public int getRetryNumber() {
        return retryNumber;
    }

    public List<Class<? extends Exception>> getExceptions() {
        return exceptions;
    }

    public TimeUnit getTimeUnit() {
        return timeUnit;
    }

    public void setTimeUnit(TimeUnit timeUnit) {
        this.timeUnit = timeUnit;
    }
}
