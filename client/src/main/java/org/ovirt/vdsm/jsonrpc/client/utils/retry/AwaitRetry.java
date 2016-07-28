package org.ovirt.vdsm.jsonrpc.client.utils.retry;

import java.util.concurrent.Callable;

import org.ovirt.vdsm.jsonrpc.client.internal.ClientPolicy;

public class AwaitRetry extends ClientPolicy {

    public AwaitRetry() {
        super(0, Integer.MAX_VALUE, 0, InterruptedException.class);
    }

    public static <T> T retry(Callable<T> callable) throws Exception {
        Callable<T> retryable = new Retryable<>(callable, new AwaitRetry());
        return retryable.call();
    }
}
