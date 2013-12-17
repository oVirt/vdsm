package org.ovirt.vdsm.jsonrpc.client.utils.retry;

import java.io.IOException;

/**
 * Default implementation of connection level retry functionality.
 *
 */
public class DefaultConnectionRetryPolicy extends RetryPolicy {
    public DefaultConnectionRetryPolicy() {
        super(2000, 0, IOException.class);
    }
}
