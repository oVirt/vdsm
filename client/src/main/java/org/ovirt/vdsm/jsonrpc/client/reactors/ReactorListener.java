package org.ovirt.vdsm.jsonrpc.client.reactors;

import java.util.concurrent.Future;

/**
 * Abstraction used for incoming message notifications.
 *
 */
public interface ReactorListener {
    public interface EventListener extends java.util.EventListener {
        public void onAcccept(ReactorListener listener, ReactorClient client);
    }

    public Future<Void> close();
}
