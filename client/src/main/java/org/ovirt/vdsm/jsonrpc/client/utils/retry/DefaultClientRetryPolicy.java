package org.ovirt.vdsm.jsonrpc.client.utils.retry;

import java.io.IOException;

/**
 * Default implementation of operation level retry functionality.
 *
 */
public class DefaultClientRetryPolicy extends RetryPolicy {
    public DefaultClientRetryPolicy() {
        super(180000, 0, 10000, IOException.class);
    }
}
