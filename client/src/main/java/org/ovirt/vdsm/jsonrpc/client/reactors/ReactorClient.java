package org.ovirt.vdsm.jsonrpc.client.reactors;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.channels.SelectionKey;
import java.nio.channels.SocketChannel;
import java.nio.charset.Charset;
import java.util.Deque;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.ConcurrentLinkedDeque;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Future;
import java.util.concurrent.FutureTask;
import java.util.concurrent.locks.Lock;
import java.util.concurrent.locks.ReentrantLock;

import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.utils.LockWrapper;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.DefaultConnectionRetryPolicy;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.RetryPolicy;
import org.ovirt.vdsm.jsonrpc.client.utils.retry.Retryable;

/**
 * Abstract implementation of <code>JsonRpcClient</code> which
 * handles low level networking.
 *
 */
public abstract class ReactorClient {
    public interface EventListener {
        public void onMessageReceived(byte[] message);
    }

    private static Log log = LogFactory.getLog(ReactorClient.class);
    private final long maxBuffLen;
    private final List<EventListener> eventListeners;
    private final ByteBuffer byteBuffer;
    private final String hostname;
    private final int port;
    private RetryPolicy policy = new DefaultConnectionRetryPolicy();
    private ByteBuffer ibuff = null;
    private final Lock lock;
    final Reactor reactor;
    SelectionKey key;
    SocketChannel channel;
    final Deque<ByteBuffer> outbox;

    public ReactorClient(Reactor reactor, String hostname, int port) {
        this.reactor = reactor;
        this.hostname = hostname;
        this.port = port;
        this.eventListeners = new CopyOnWriteArrayList<EventListener>();
        this.lock = new ReentrantLock();
        this.outbox = new ConcurrentLinkedDeque<>();
        this.byteBuffer = ByteBuffer.allocate(8);
        this.byteBuffer.order(ByteOrder.BIG_ENDIAN);
        this.byteBuffer.rewind();
        this.maxBuffLen = (1 << 20) * 4;
    }

    public String getHostname() {
        return this.hostname;
    }

    public void setRetryPolicy(RetryPolicy policy) {
        this.policy = policy;
    }

    public void connect() throws ClientConnectionException {
        if (isOpen()) {
            return;
        }
        try (LockWrapper wrapper = new LockWrapper(this.lock)) {
            if (isOpen()) {
                return;
            }
            final FutureTask<SocketChannel> task = new FutureTask<>(
                    new Retryable<SocketChannel>(new Callable<SocketChannel>() {
                        @Override
                        public SocketChannel call() throws IOException {

                            InetAddress address = InetAddress.getByName(hostname);
                            log.info("Connecting to " + address);

                            final InetSocketAddress addr = new InetSocketAddress(address, port);
                            final SocketChannel socketChannel = SocketChannel.open();

                            socketChannel.connect(addr);
                            socketChannel.configureBlocking(false);
                            return socketChannel;
                        }
                    }, this.policy));
            this.reactor.queueFuture(task);
            this.channel = task.get();
            postConnect();
        } catch (InterruptedException | ExecutionException e) {
            log.error("Exception during connection", e);
            throw new ClientConnectionException(e);
        }
    }

    public SelectionKey getSelectionKey() throws ClientConnectionException {
        if (this.key == null) {
            connect();
        }
        return this.key;
    }

    public void addEventListener(EventListener el) {
        eventListeners.add(el);
    }

    public void removeEventListener(EventListener el) {
        eventListeners.remove(el);
    }

    public void sendMessage(byte[] message) {
        ByteBuffer messageBuf = ByteBuffer.wrap(message);
        messageBuf = messageBuf.slice();
        log.info("Message sent: " + new String(message, Charset.forName("UTF-8")));
        ByteBuffer buffer = ByteBuffer.allocate(8);
        buffer.order(ByteOrder.BIG_ENDIAN);
        buffer.putLong(messageBuf.remaining());
        buffer.rewind();
        outbox.addFirst(buffer);
        outbox.addFirst(messageBuf);

        final ReactorClient client = this;
        reactor.queueFuture(new FutureTask<>(new Callable<Void>() {
            @Override
            public Void call() throws ClientConnectionException {
                client.updateInterestedOps();
                return null;
            }
        }));
    }

    private void emitOnMessageReceived(byte[] message) {
        for (EventListener el : eventListeners) {
            el.onMessageReceived(message);
        }
    }

    public Future<Void> close() {
        final FutureTask<Void> t = new FutureTask<>(new Callable<Void>() {
            @Override
            public Void call() {
                closeChannel();
                return null;
            }
        });
        reactor.queueFuture(t);
        return t;
    }

    private void readBytes(ByteBuffer ibuff) throws IOException, ClientConnectionException {
        readBuffer(ibuff);

        if (ibuff.hasRemaining()) {
            return;
        }

        ibuff.rewind();
        emitOnMessageReceived(ibuff.array());
        this.ibuff = null;
        this.byteBuffer.clear();
    }

    public void process() throws IOException, ClientConnectionException {
        processIncoming();
        processOutgoing();
    }

    private void processIncoming() throws IOException, ClientConnectionException {
        if (this.ibuff == null) {
            readBuffer(byteBuffer);

            if (byteBuffer.hasRemaining()) {
                return;
            }
            byteBuffer.rewind();
            long len = byteBuffer.getLong();
            if (len < 0 || len > maxBuffLen) {
                closeChannel();
            } else {
                this.ibuff = ByteBuffer.allocate((int) len);
                this.ibuff.order(ByteOrder.BIG_ENDIAN);
                readBytes(this.ibuff);
            }
        } else {
            readBytes(this.ibuff);
        }
    }

    private void processOutgoing() throws IOException, ClientConnectionException {
        final ByteBuffer buff = outbox.peekLast();

        if (buff == null) {
            return;
        }

        writeBuffer(buff);

        if (!buff.hasRemaining()) {
            outbox.removeLast();
        }
        updateInterestedOps();
    }

    private void closeChannel() {
        try {
            this.channel.close();
        } catch (IOException e) {
            // Ignore
        }
    }

    public boolean isOpen() {
        return channel != null && channel.isOpen();
    }

    private void readBuffer(ByteBuffer buff) throws IOException, ClientConnectionException {
        if (!isOpen()) {
            connect();
        }
        read(buff);
    }

    private void writeBuffer(ByteBuffer buff) throws IOException, ClientConnectionException {
        if (!isOpen()) {
            connect();
        }
        write(buff);
    }

    /**
     * Reads provided buffer.
     * @param buff provided buffer to be read.
     * @throws IOException when networking issue occurs.
     */
    abstract void read(ByteBuffer buff) throws IOException;

    /**
     * Writes provided buffer.
     * @param buff provided buffer to be written.
     * @throws IOException when networking issue occurs.
     */
    abstract void write(ByteBuffer buff) throws IOException;

    /**
     * Transport specific post connection functionality.
     * @throws ClientConnectionException when issues with connection.
     */
    abstract void postConnect() throws ClientConnectionException;

    /**
     * Updates selection key's operation set.
     * @throws ClientConnectionException when issues with connection.
     */
    abstract void updateInterestedOps() throws ClientConnectionException;
}
