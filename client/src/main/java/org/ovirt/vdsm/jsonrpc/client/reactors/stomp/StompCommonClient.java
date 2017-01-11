package org.ovirt.vdsm.jsonrpc.client.reactors.stomp;

import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.END_OF_MESSAGE;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_HEART_BEAT;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_ID;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_MESSAGE;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEADER_RECEIPT;
import static org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.HEARTBEAT_FRAME;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.UTF8;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.addGracePeriod;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.buildErrorResponse;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.isEmpty;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.reduceGracePeriod;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Future;

import org.ovirt.vdsm.jsonrpc.client.ClientConnectionException;
import org.ovirt.vdsm.jsonrpc.client.JsonRpcResponse;
import org.ovirt.vdsm.jsonrpc.client.internal.ClientPolicy;
import org.ovirt.vdsm.jsonrpc.client.reactors.Reactor;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message;
import org.ovirt.vdsm.jsonrpc.client.reactors.stomp.impl.Message.Command;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public abstract class StompCommonClient extends ReactorClient {
    public static final String DEFAULT_REQUEST_QUEUE = "jms.queue.requests";
    public static final String DEFAULT_RESPONSE_QUEUE = "jms.queue.reponses";
    protected ByteBuffer headerBuffer = ByteBuffer.allocate(BUFFER_SIZE);
    protected Message message;
    protected CountDownLatch connected;
    protected CountDownLatch subscribed;
    protected List<String> subscriptionIds = new ArrayList<>();
    private static final Logger LOG = LoggerFactory.getLogger(StompCommonClient.class);

    public StompCommonClient(Reactor reactor, String hostname, int port) {
        super(reactor, hostname, port);
    }

    public void send(byte[] message) {
        outbox.addFirst(ByteBuffer.wrap(message));
        updateOps(message);
    }

    private void updateOps(byte[] message) {
        if (LOG.isDebugEnabled()) {
            try {
                LOG.debug("Message sent: " + Message.parse(message));
            } catch (ClientConnectionException ignored) {
            }
        }

        final ReactorClient client = this;
        scheduleTask(() -> {
            client.updateInterestedOps();
            return null;
        });
    }

    public void sendNow(byte[] message) {
        outbox.addLast(ByteBuffer.wrap(message));
        updateOps(message);
    }

    void processMessage(Message message) {
        if (Command.CONNECTED.toString().equals(message.getCommand())) {
            updatePolicyWithHeartbeat(message.getHeaders().get(HEADER_HEART_BEAT), true);
            this.connected.countDown();
        } else if (Command.ACK.toString().equals(message.getCommand())) {
            String headerId = message.getHeaders().get(HEADER_ID);
            if (!isEmpty(headerId)) {
                this.subscribed.countDown();
            }
        } else if (Command.ERROR.toString().equals(message.getCommand())) {
            String errorMessage = message.getHeaders().get(HEADER_MESSAGE);
            StringBuilder error = new StringBuilder();
            if (!isEmpty(errorMessage)) {
                error.append(errorMessage);
            }
            if (message.getContent().length == 0) {
                error.append(errorMessage);
            }
            LOG.error("Error Message recieved: " + error);
        } else if (Command.MESSAGE.toString().equals(message.getCommand())) {
            super.emitOnMessageReceived(message.getContent());
        }
    }

    @Override
    public Future<Void> close() {
        clean();
        subscriptionIds.stream().forEach(
                subscriptionId -> send(new Message().unsubscribe().withHeader(HEADER_ID, subscriptionId).build()));
        send(new Message().disconnect().withHeader(HEADER_RECEIPT, UUID.randomUUID().toString()).build());
        return super.close();
    }

    @Override
    protected void processIncoming() throws IOException, ClientConnectionException {
        if (this.ibuff == null) {
            int read = read(headerBuffer);
            if (read <= 0) {
                return;
            }
            updateLastIncomingHeartbeat();

            this.message = getMessage(headerBuffer, headerBuffer.position());
            if (this.message == null) {
                return;
            }
            int contentLength = this.message.getContentLength();
            if (contentLength == -1) {
                // only for control messages, all other have the header
                // according to stomp spec: The commands and headers are encoded in UTF-8
                String[] messages = new String(headerBuffer.array(), UTF8).split(END_OF_MESSAGE);
                for (String msg : messages) {
                    Message mesg = Message.parse((msg + END_OF_MESSAGE).getBytes(UTF8));
                    int contLen = mesg.getContentLength();
                    if (contLen != -1 && contLen != mesg.getContent().length - 1) {
                        this.message = mesg;
                        break;
                    }
                    emitOnMessageReceived(mesg);
                }
                return;
            }
            int length = this.message.getContent().length;
            if (contentLength == length - 1) {
                emitOnMessageReceived(this.message);
                return;
            } else if (contentLength > length) {
                this.ibuff = ByteBuffer.allocate(contentLength - length + 1);
            } else {
                byte[] content = this.message.getContent();
                this.message.withContent(Arrays.copyOfRange(content, 0, contentLength + 1));
                emitOnMessageReceived(this.message);
                headerBuffer.put(Arrays.copyOfRange(content, contentLength + 1, content.length));
                return;
            }
        }

        read(this.ibuff);
        updateLastIncomingHeartbeat();
        int length = this.message.getContent().length + this.ibuff.position();
        if (this.message.getContentLength() != length - 1) {
            return;
        }
        this.message.withAdditionalContent(this.ibuff.array());
        emitOnMessageReceived(this.message);
    }

    private Message getMessage(ByteBuffer buffer, int read) throws ClientConnectionException {
        if (read > BUFFER_SIZE) {
            read = BUFFER_SIZE;
        }
        byte[] array = new byte[read];
        buffer.rewind();
        buffer.get(array);
        return Message.parse(array);
    }

    protected void clean() {
        headerBuffer = ByteBuffer.allocate(BUFFER_SIZE);
        this.ibuff = null;
        this.message = null;
    }

    protected void emitOnMessageReceived(Message message) {
        message.trimEndOfMessage();
        clean();
        processMessage(message);
    }

    @Override
    protected byte[] buildNetworkResponse(String reason) {
        JsonRpcResponse response = buildErrorResponse(null, getClientId(), reason);
        return response.toByteArray();
    }

    public void updatePolicyWithHeartbeat(String heartbeat, boolean client) {
        if (!isEmpty(heartbeat)) {
            String[] heartbeats = heartbeat.split(",");
            try {
                int outgoing = Integer.parseInt(heartbeats[client ? 1 : 0]);
                int incoming = Integer.parseInt(heartbeats[client ? 0 : 1]);
                if (policy.getOutgoingHeartbeat() != outgoing) {
                    policy.setOutgoingHeartbeat(reduceGracePeriod(outgoing));
                }
                if (policy.getIncomingHeartbeat() != incoming) {
                    policy.setIncomingHeartbeat(addGracePeriod(incoming));
                }
            } catch (NumberFormatException ignored) {
            }
        }
    }

    @Override
    protected void sendHeartbeat() {
        this.send(HEARTBEAT_FRAME);
    }

    public void validate(ClientPolicy policy) {
        if (!StompClientPolicy.class.isInstance(policy)) {
            throw new IllegalStateException("Wrong policy type");
        }
    }

    public String getRequestQueue() {
        return ((StompClientPolicy)this.policy).getRequestQueue();
    }

    public String getResponseQueue() {
        return ((StompClientPolicy)this.policy).getResponseQueue();
    }

    public String getEventQueue() {
        return ((StompClientPolicy)this.policy).getEventQueue();
    }
}
