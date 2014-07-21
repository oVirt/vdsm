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
    private final int heartbeat;
    private List<Class<? extends Exception>> exceptions;
    private TimeUnit timeUnit = TimeUnit.MILLISECONDS;

    /**
     * Create policy using provided values.
     * @param retryTimeOut - <code>Integer</code> value which is used as timeout between operation retry
     *                       combined with <code>TimeUnit</code> which is set to milliseconds by default.
     * @param retryNumber - <code>Integer</code> value which defines number of retry attempts.
     * @param heartbeat - <code>Integer</code> value which defines heart beat.
     * @param retryableExceptions - <code>List</code> of retryable exceptions.
     */
    public RetryPolicy(int retryTimeOut, int retryNumber, int heartbeat, List<Class<? extends Exception>> retryableExceptions) {
        this.retryNumber = retryNumber;
        this.retryTimeOut = retryTimeOut;
        this.heartbeat = heartbeat;
        this.exceptions = Collections.unmodifiableList(retryableExceptions);
    }

    public RetryPolicy(int retryTimeOut, int retryNumber, int heartbeat) {
        this(retryTimeOut, retryNumber, heartbeat, new ArrayList<Class<? extends Exception>>());
    }

    public RetryPolicy(int retryTimeOut, int retryNumber, int heartbeat, Class<? extends Exception> retryableException) {
        this(retryTimeOut, retryNumber, heartbeat, new ArrayList<Class<? extends Exception>>(Arrays.asList(retryableException)));
    }

    public int getRetryTimeOut() {
        return this.retryTimeOut;
    }

    public int getRetryNumber() {
        return this.retryNumber;
    }

    public int getHeartbeat() {
        return this.heartbeat;
    }

    public List<Class<? extends Exception>> getExceptions() {
        return this.exceptions;
    }

    public TimeUnit getTimeUnit() {
        return this.timeUnit;
    }

    public void setTimeUnit(TimeUnit timeUnit) {
        this.timeUnit = timeUnit;
    }
}
