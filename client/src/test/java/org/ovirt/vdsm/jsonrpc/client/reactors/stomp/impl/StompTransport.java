package org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl;

import static org.junit.Assert.fail;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.END_OF_MESSAGE;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.UTF8;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.ByteBuffer;
import java.nio.channels.SelectionKey;
import java.nio.channels.ServerSocketChannel;
import java.nio.channels.SocketChannel;
import java.nio.channels.spi.AbstractSelector;
import java.nio.channels.spi.SelectorProvider;
import java.util.Deque;
import java.util.concurrent.ConcurrentLinkedDeque;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;


public class StompTransport extends Thread implements TestSender {
    private String host;
    private int port;
    private ByteBuffer readBuffer = ByteBuffer.allocateDirect(4096);
    private AbstractSelector selector;
    private boolean isRunning = true;
    private Reciever reciever;

    public StompTransport(String host, Reciever reciever) throws IOException {
        this.selector = SelectorProvider.provider().openSelector();
        this.reciever = reciever;
        this.host = host;
    }

    public SelectionKey connect(int port) throws IOException {
        final InetSocketAddress addr = new InetSocketAddress(InetAddress.getByName(this.host), port);
        SocketChannel socketChannel = SocketChannel.open();

        socketChannel.connect(addr);
        socketChannel.configureBlocking(false);

        int interestedOps = SelectionKey.OP_READ;
        SelectionKey key = socketChannel.register(this.selector, interestedOps, new ConcurrentLinkedDeque<>());

        setDaemon(true);
        start();
        return key;
    }

    public void listen() throws IOException {
        ServerSocketChannel serverSocketChannel = ServerSocketChannel.open();
        serverSocketChannel.configureBlocking(false);

        serverSocketChannel.register(this.selector, SelectionKey.OP_ACCEPT, new ConcurrentLinkedDeque<>());
        serverSocketChannel.bind(new InetSocketAddress(this.host, 0));

        this.port = serverSocketChannel.socket().getLocalPort();

        setDaemon(true);
        start();
    }

    @SuppressWarnings("unchecked")
    public void send(byte[] message, SelectionKey key) {
        Deque<ByteBuffer> outbox = (Deque<ByteBuffer>) key.attachment();
        ByteBuffer buffer = ByteBuffer.wrap(message);
        outbox.addFirst(buffer);
        updateInterestedOps(key);
        this.selector.wakeup();
    }

    public void close() throws IOException {
        this.isRunning = false;
        this.selector.close();
        this.selector.wakeup();
    }

    @SuppressWarnings("unchecked")
    @Override
    public void run() {
        while (this.isRunning) {
            try {
                this.selector.select();

                if (!selector.isOpen()) {
                    continue;
                }
                for (final SelectionKey key : this.selector.selectedKeys()) {
                    if (key.isValid() && key.isAcceptable()) {
                        ServerSocketChannel serverSocketChannel = (ServerSocketChannel) key.channel();
                        SocketChannel socketChannel = serverSocketChannel.accept();
                        if (socketChannel != null) {
                            socketChannel.configureBlocking(false);

                            int interestedOps = SelectionKey.OP_READ;
                            socketChannel.register(selector, interestedOps, new ConcurrentLinkedDeque<>());
                        }
                    }

                    if (key.isValid() && key.isReadable()) {
                        SocketChannel socketChannel = (SocketChannel) key.channel();
                        int read = socketChannel.read(this.readBuffer);
                        if (read > 0) {
                            byte[] msgBuff = new byte[read];
                            this.readBuffer.rewind();
                            this.readBuffer.get(msgBuff);
                            this.readBuffer.clear();
                            String[] messages = new String(msgBuff, UTF8).split(END_OF_MESSAGE);
                            for (String message : messages) {
                                message = message + END_OF_MESSAGE;
                                try {
                                    this.reciever.recieve(Message.parse(message.getBytes(UTF8)), key);
                                } catch (ClientConnectionException e) {
                                    fail();
                                }
                            }
                        }
                    }
                    if (key.isValid() && key.isWritable()) {
                        Deque<ByteBuffer> outbox = (Deque<ByteBuffer>) key.attachment();
                        ByteBuffer buffer = outbox.pollLast();

                        if (buffer != null) {
                            SocketChannel socketChannel = (SocketChannel) key.channel();
                            socketChannel.write(buffer);
                            updateInterestedOps(key);
                        }
                    }

                    if (!key.channel().isOpen()) {
                        key.cancel();
                    }
                }
            } catch (IOException e) {
                e.printStackTrace();
            }
        }
    }

    @SuppressWarnings("unchecked")
    public void updateInterestedOps(SelectionKey key) {
        Deque<ByteBuffer> outbox = (Deque<ByteBuffer>) key.attachment();
        if (outbox.isEmpty()) {
            key.interestOps(SelectionKey.OP_READ);
        } else {
            key.interestOps(SelectionKey.OP_READ | SelectionKey.OP_WRITE);
        }
    }

    public int getPort() {
        return port;
    }
}
