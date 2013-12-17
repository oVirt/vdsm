package org.ovirt.vdsm.jsonrpc.client.reactors;

import java.io.IOException;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;

import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLEngine;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;

/**
 * Implementation of {@link Reactor} for secured connections.
 *
 */
public final class SSLReactor extends Reactor {

    private final SSLContext sslContext;

    public SSLReactor(SSLContext sslctx) throws IOException {
        this.sslContext = sslctx;
    }

    private SSLEngine createSSLEngine(boolean clientMode) {
        final SSLContext ctx = this.sslContext;
        if (ctx == null) {
            return null;
        }
        final SSLEngine engine = ctx.createSSLEngine();
        engine.setUseClientMode(clientMode);
        return engine;
    }

    @Override
    public ReactorClient createClient(Reactor reactor, Selector selector, String hostname, int port)
            throws ClientConnectionException {
        return new SSLClient(reactor, selector, hostname, port, createSSLEngine(true));
    }

    @Override
    public ReactorClient createConnectedClient(Reactor reactor, Selector selector, String hostname,
            int port, SocketChannel channel) throws ClientConnectionException {
        return new SSLClient(reactor, selector, hostname, port, createSSLEngine(true), channel);
    }

    @Override
    public String getReactorName() {
        return "SSL Reactor";
    }
}
