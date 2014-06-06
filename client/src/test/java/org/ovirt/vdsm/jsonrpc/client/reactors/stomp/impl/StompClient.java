package org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl;

import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ACCEPT;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_DESTINATION;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ID;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_RECEIPT;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_RECEIPT_ID;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_TRANSACTION;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.UTF8;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.isEmpty;

import java.io.IOException;
import java.nio.channels.SelectionKey;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.Command;

public class StompClient implements Reciever {
    private StompTransport transport;
    private CountDownLatch connected = new CountDownLatch(1);
    private CountDownLatch disconnected = new CountDownLatch(1);
    private SelectionKey key;
    private Map<String, Listener> listener = new ConcurrentHashMap<>();
    private Map<String, String> destinations = new ConcurrentHashMap<>();
    private String id;
    private String transactionId;

    public StompClient(String host, int port) throws IOException {
        this.transport = new StompTransport(host, port, this);
        this.key = this.transport.connect();
        this.transport.send(new Message().connect().withHeader(HEADER_ACCEPT, "1.2").build(), key);
        try {
            // TODO use connection timeout
            this.connected.await();
        } catch (InterruptedException e) {
            throw new IOException("Not connected");
        }
    }

    public void subscribe(String channel, Listener listener) {
        if (this.listener.get(channel) != null) {
            throw new IllegalArgumentException("Already subscribed to channel: " + channel);
        }
        this.listener.put(channel, listener);
        String id = UUID.randomUUID().toString();
        this.destinations.put(channel, id);
        this.transport.send(new Message().subscribe()
                .withHeader(HEADER_DESTINATION, channel)
                .withHeader(HEADER_ID, id)
                .build(), this.key);
    }

    public void send(String content, String channel) {
        Map<String, String> headers = new HashMap<>();
        if (!isEmpty(this.transactionId)) {
            headers.put(HEADER_TRANSACTION, this.transactionId);
        }
        headers.put(HEADER_DESTINATION, channel);
        this.transport.send(new Message().send().withContent(content.getBytes(UTF8)).withHeaders(headers).build(),
                key);
    }

    public void unsubscribe(String channel) {
        String id = this.destinations.remove(channel);
        this.transport.send(new Message().unsubscribe().withHeader(HEADER_ID, id).build(), key);
        this.listener.remove(channel);
    }

    public void disconnect() throws IOException {
        id = UUID.randomUUID().toString();
        this.transport.send(new Message().disconnect().withHeader(HEADER_RECEIPT, id).build(), key);
        try {
            // TODO message timeout
            this.disconnected.await(1, TimeUnit.SECONDS);
        } catch (InterruptedException ignored) {
            // we can never receive confirmation
        }
    }

    public void stop() throws IOException {
        this.transport.close();
        this.listener.clear();
        this.destinations.clear();
    }

    public void begin() {
        if (!isEmpty(this.transactionId)) {
            throw new IllegalStateException("Already opened transaction");
        }
        this.transactionId = UUID.randomUUID().toString();
        this.transport.send(new Message().begin().withHeader(HEADER_TRANSACTION, this.transactionId).build(), key);
    }

    public void commit() {
        if (isEmpty(this.transactionId)) {
            throw new IllegalStateException("No running transaction");
        }
        this.transport.send(new Message().commit().withHeader(HEADER_TRANSACTION, this.transactionId).build(), key);
        this.transactionId = null;
    }

    public void abort() {
        if (isEmpty(this.transactionId)) {
            throw new IllegalStateException("No running transaction");
        }
        this.transport.send(new Message().abort().withHeader(HEADER_TRANSACTION, this.transactionId).build(), key);
        this.transactionId = null;
    }

    @Override
    public void recieve(Message message, SelectionKey key) {
        if (Command.CONNECTED.toString().equals(message.getCommand())) {
            this.connected.countDown();
        } else if (Command.MESSAGE.toString().equals(message.getCommand())) {
            String destination = message.getHeaders().get(HEADER_DESTINATION);
            Listener listener = this.listener.get(destination);
            if (listener != null) {
                listener.update(new String(message.getContent(), UTF8));
            }
        } else if (Command.ERROR.toString().equals(message.getCommand())) {
            String destination = message.getHeaders().get(HEADER_DESTINATION);
            Listener listener = this.listener.get(destination);
            if (listener != null) {
                listener.error(message.getHeaders());
            }
        } else if (Command.RECEIPT.toString().equals(message.getCommand())) {
            String receiptId = message.getHeaders().get(HEADER_RECEIPT_ID);
            if (!isEmpty(receiptId) && id.equals(receiptId)) {
                this.disconnected.countDown();
            }
        }
    }
}
