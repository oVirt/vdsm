package org.ovirt.vdsm.jsonrpc.client.reactors;

import java.io.IOException;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;

/**
 * Implementation of {@link Reactor} for plain connections.
 *
 */
public final class NioReactor extends Reactor {

    public NioReactor() throws IOException {
        super();
    }

    @Override
    public ReactorClient createClient(Reactor reactor, Selector selector, String hostname, int port)
            throws ClientConnectionException {
        return new NioClient(reactor, selector, hostname, port);
    }

    @Override
    public ReactorClient createConnectedClient(Reactor reactor, Selector selector, String hostname,
            int port, SocketChannel channel) throws ClientConnectionException {
        return new NioClient(reactor, selector, hostname, port, channel);
    }

    @Override
    public String getReactorName() {
        return "Plain Reactor";
    }
}
