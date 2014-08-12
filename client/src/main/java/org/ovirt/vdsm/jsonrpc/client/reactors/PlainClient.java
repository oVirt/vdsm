package org.ovirt.vdsm.jsonrpc.client.reactors;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.ClosedChannelException;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.StompCommonClient;
import org.ovirt.vdsm.jsonrpc.client.utils.OneTimeCallback;

/**
 * <code>ReactorClient</code> implementation to provide not encrypted communication.
 *
 */
public abstract class PlainClient extends StompCommonClient {
    protected final Selector selector;

    public PlainClient(Reactor reactor, Selector selector, String hostname, int port) throws ClientConnectionException {
        super(reactor, hostname, port);
        this.selector = selector;
    }

    public PlainClient(Reactor reactor, Selector selector, String hostname, int port, SocketChannel socketChannel)
            throws ClientConnectionException {
        super(reactor, hostname, port);
        this.selector = selector;

        channel = socketChannel;
        postConnect(getPostConnectCallback());
    }

    @Override
    public void updateInterestedOps() throws ClientConnectionException {
        if (outbox.isEmpty()) {
            getSelectionKey().interestOps(SelectionKey.OP_READ);
        } else {
            getSelectionKey().interestOps(SelectionKey.OP_READ | SelectionKey.OP_WRITE);
        }
    }

    @Override
    int read(ByteBuffer buff) throws IOException {
        return channel.read(buff);
    }

    @Override
    void write(ByteBuffer buff) throws IOException {
        channel.write(buff);
    }

    @Override
    protected void postConnect(OneTimeCallback callback) throws ClientConnectionException {
        try {
            int interestedOps = SelectionKey.OP_READ;
            reactor.wakeup();
            key = this.channel.register(selector, interestedOps, this);
        } catch (ClosedChannelException e) {
            throw new ClientConnectionException(e);
        }
    }

    @Override
    public void clearBuff() {
        outbox.clear();
    }
}
