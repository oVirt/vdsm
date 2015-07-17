package org.ovirt.vdsm.jsonrpc.client.utils.retry;

import java.util.concurrent.Callable;

import org.ovirt.vdsm.jsonrpc.client.internal.ClientPolicy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Wrapper of any {@link Callable} which retries call method execution based on provided <code>RetryPolicy</code>.
 *
 * @param <T> Result type.
 */
public class Retryable<T> implements Callable<T> {
    private static Logger log = LoggerFactory.getLogger(Retryable.class);
    private Callable<T> callable;
    private RetryContext context;

    public Retryable(Callable<T> callable, ClientPolicy policy) {
        this.callable = callable;
        this.context = new RetryContext(policy);
    }

    public T call() throws Exception {
        while (true) {
            try {
                return this.callable.call();
            } catch (Exception e) {
                log.warn("Retry failed");
                if (log.isDebugEnabled()) {
                    log.debug(e.getMessage(), e);
                }
                if (this.context.isExceptionRetryable(e)) {
                    this.context.decreaseAttempts();
                    if (this.context.getNumberOfAttempts() <= 0) {
                        throw e;
                    }
                    this.context.waitOperation();
                    continue;
                } else {
                    throw e;
                }
            }
        }
    }

}
