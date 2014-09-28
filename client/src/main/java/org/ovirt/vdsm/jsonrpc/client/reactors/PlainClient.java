package org.ovirt.vdsm.jsonrpc.client.reactors;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.ClosedChannelException;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.FutureTask;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.StompCommonClient;
import org.ovirt.vdsm.jsonrpc.client.utils.OneTimeCallback;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.Retryable;

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
    public void updateInterestedOps() {
        if (outbox.isEmpty()) {
            getSelectionKey().interestOps(SelectionKey.OP_READ);
        } else {
            getSelectionKey().interestOps(SelectionKey.OP_READ | SelectionKey.OP_WRITE);
        }
    }

    @Override
    protected int read(ByteBuffer buff) throws IOException {
        return channel.read(buff);
    }

    @Override
    void write(ByteBuffer buff) throws IOException {
        channel.write(buff);
    }

    @Override
    protected void postConnect(OneTimeCallback callback) throws ClientConnectionException {
        try {
            final ReactorClient client = this;
            final FutureTask<SelectionKey> task = scheduleTask(new Retryable<SelectionKey>(
                    new Callable<SelectionKey>() {

                        @Override
                        public SelectionKey call() throws ClosedChannelException {
                            return channel.register(selector, SelectionKey.OP_READ, client);
                        }
                    }, this.policy));

            key = task.get();
        } catch (InterruptedException | ExecutionException e) {
            throw new ClientConnectionException(e);
        }
        if (key == null) {
            throw new ClientConnectionException("Connection issue during post connect");
        }
    }

    @Override
    public void postDisconnect() {
        outbox.clear();
    }
}
