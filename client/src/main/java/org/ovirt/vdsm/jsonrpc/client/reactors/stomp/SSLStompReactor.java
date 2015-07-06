package org.ovirt.vdsm.jsonrpc.client.reactors.stomp;

import java.io.IOException;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;

import javax.net.ssl.SSLContext;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient;

public class SSLStompReactor extends Reactor {

    private SSLContext sslContext;

    public SSLStompReactor(SSLContext sslContext) throws IOException {
        super();
        this.sslContext = sslContext;
    }

    @Override
    public String getReactorName() {
        return "SSL Stomp Reactor";
    }

    @Override
    public ReactorClient createClient(Reactor reactor, Selector selector, String hostname, int port)
            throws ClientConnectionException {
        return new SSLStompClient(reactor, selector, hostname, port, this.sslContext);
    }

    @Override
    public ReactorClient createConnectedClient(Reactor reactor,
            Selector selector,
            String hostname,
            int port,
            SocketChannel channel) throws ClientConnectionException {
        return new SSLStompListener(reactor, selector, hostname, port, channel, this.sslContext);
    }

}
