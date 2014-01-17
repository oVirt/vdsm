package org.ovirt.vdsm.jsonrpc.client.reactors;

import java.io.IOException;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;

import javax.net.ssl.SSLContext;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;

/**
 * Implementation of {@link Reactor} for secured connections.
 *
 */
public final class SSLReactor extends Reactor {
    private final static String REACTOR_NAME = "SSL Reactor";

    private final SSLContext sslContext;

    public SSLReactor(SSLContext sslctx) throws IOException {
        this.sslContext = sslctx;
    }

    @Override
    public ReactorClient createClient(Reactor reactor, Selector selector, String hostname, int port)
            throws ClientConnectionException {
        return new SSLClient(reactor, selector, hostname, port, this.sslContext);
    }

    @Override
    public ReactorClient createConnectedClient(Reactor reactor, Selector selector, String hostname,
            int port, SocketChannel channel) throws ClientConnectionException {
        return new SSLClient(reactor, selector, hostname, port, this.sslContext, channel);
    }

    @Override
    public String getReactorName() {
        return REACTOR_NAME;
    }
}
