package org.ovirt.vdsm.jsonrpc.server;

import static org.junit.Assert.fail;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.ServerSocketChannel;
import java.nio.channels.SocketChannel;
import java.nio.channels.spi.SelectorProvider;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

public class JsonRpcServer extends Thread {
    private int port;

    private ServerSocketChannel serverChannel;

    private Selector selector;

    private Worker worker;

    private List<ChangeRequest> pendingChanges = new CopyOnWriteArrayList<>();

    private Map<SocketChannel, List<ByteBuffer>> pendingData = new ConcurrentHashMap<>();
    private boolean isDone = true;

    private ByteBuffer byteBuffer;
    private ByteBuffer ibuff;
    private final long maxBuffLen;

    public JsonRpcServer(int port) throws IOException {
        this.port = port;
        this.selector = this.initSelector();
        this.worker = new Worker();
        this.worker.setDaemon(true);
        this.worker.start();
        setDaemon(true);
        start();

        this.byteBuffer = ByteBuffer.allocate(8);
        this.byteBuffer.order(ByteOrder.BIG_ENDIAN);
        this.byteBuffer.rewind();
        this.ibuff = null;
        this.maxBuffLen = (1 << 20) * 4;
    }

    public void send(SocketChannel socket, byte[] data) {
        this.pendingChanges.add(new ChangeRequest(socket, ChangeRequest.CHANGEOPS, SelectionKey.OP_WRITE));

        List<ByteBuffer> queue = this.pendingData.get(socket);
        if (queue == null) {
            queue = new ArrayList<ByteBuffer>();
            this.pendingData.put(socket, queue);
        }
        ByteBuffer dataBuffer = ByteBuffer.wrap(data);
        ByteBuffer buffer = ByteBuffer.allocate(8);
        buffer.order(ByteOrder.BIG_ENDIAN);
        buffer.putLong(dataBuffer.remaining());
        buffer.rewind();
        queue.add(buffer);
        queue.add(dataBuffer);

        this.selector.wakeup();
    }

    public void close() {
        this.isDone = false;
        this.selector.wakeup();
    }

    public void run() {
        while (this.isDone) {
            try {
                Iterator<ChangeRequest> changes = this.pendingChanges.iterator();
                while (changes.hasNext()) {
                    ChangeRequest change = changes.next();
                    switch (change.type) {
                    case ChangeRequest.CHANGEOPS:
                        SelectionKey key = change.socket.keyFor(this.selector);
                        key.interestOps(change.ops);
                    }
                }
                this.pendingChanges.clear();

                this.selector.select();

                Iterator<SelectionKey> selectedKeys = this.selector.selectedKeys().iterator();
                while (selectedKeys.hasNext()) {
                    SelectionKey key = selectedKeys.next();
                    selectedKeys.remove();

                    if (!key.isValid()) {
                        continue;
                    }

                    if (key.isAcceptable()) {
                        this.accept(key);
                    } else if (key.isReadable()) {
                        this.read(key);
                    } else if (key.isWritable()) {
                        this.write(key);
                    }
                }
            } catch (Exception e) {
                fail();
            }
        }
        try {
            this.serverChannel.close();
        } catch (IOException ignored) {
        }
    }

    private void accept(SelectionKey key) throws IOException {
        ServerSocketChannel serverSocketChannel = (ServerSocketChannel) key.channel();

        SocketChannel socketChannel = serverSocketChannel.accept();
        socketChannel.configureBlocking(false);

        socketChannel.register(this.selector, SelectionKey.OP_READ);
    }

    private void readSize(SocketChannel socketChannel) throws IOException {
        socketChannel.read(this.byteBuffer);

        if (this.byteBuffer.hasRemaining()) {
            return;
        }
        this.byteBuffer.rewind();
        long len = this.byteBuffer.getLong();

        if (len < 0 || len > this.maxBuffLen) {
            socketChannel.close();
        } else {
            this.ibuff = ByteBuffer.allocate((int) len);
            this.ibuff.order(ByteOrder.BIG_ENDIAN);
        }
    }

    private void processIncoming(SocketChannel socketChannel) throws IOException {
        boolean prev = !(this.ibuff == null);
        while (this.ibuff == null != prev) {
            prev = (this.ibuff == null);
            if (this.ibuff == null) {
                readSize(socketChannel);
            } else {
                readMessage(socketChannel);
            }
        }
    }

    private void readMessage(SocketChannel socketChannel) throws IOException {
        assert this.ibuff != null;
        socketChannel.read(this.ibuff);

        if (this.ibuff.hasRemaining()) {
            return;
        }

        this.ibuff.rewind();
        this.worker.processData(this, socketChannel, this.ibuff.array());
        this.ibuff = null;
        this.byteBuffer.clear();
    }

    private void read(SelectionKey key) throws IOException {
        SocketChannel socketChannel = (SocketChannel) key.channel();
        processIncoming(socketChannel);
    }

    private void write(SelectionKey key) throws IOException {
        SocketChannel socketChannel = (SocketChannel) key.channel();

        List<ByteBuffer> queue = this.pendingData.get(socketChannel);

        while (!queue.isEmpty()) {
            ByteBuffer buf = (ByteBuffer) queue.get(0);
            socketChannel.write(buf);
            if (buf.remaining() > 0) {
                break;
            }
            queue.remove(0);
        }

        if (queue.isEmpty()) {
            key.interestOps(SelectionKey.OP_READ);
        }
    }

    private Selector initSelector() throws IOException {
        Selector socketSelector = SelectorProvider.provider().openSelector();

        this.serverChannel = ServerSocketChannel.open();
        this.serverChannel.configureBlocking(false);

        this.serverChannel.socket().bind(new InetSocketAddress(this.port));
        this.serverChannel.register(socketSelector, SelectionKey.OP_ACCEPT);

        return socketSelector;
    }
}
