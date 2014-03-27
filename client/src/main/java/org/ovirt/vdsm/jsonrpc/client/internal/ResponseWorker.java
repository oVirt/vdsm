package org.ovirt.vdsm.jsonrpc.client.internal;

import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.UTF8;

import java.util.Iterator;
import java.util.concurrent.LinkedBlockingQueue;

import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.codehaus.jackson.JsonNode;
import org.codehaus.jackson.map.ObjectMapper;
import org.ovirt.vdsm.jsonrpc.client.JsonRpcClient;
import org.ovirt.vdsm.jsonrpc.client.JsonRpcResponse;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorClient.MessageListener;
import org.ovirt.vdsm.jsonrpc.client.reactors.ReactorFactory;

/**
 * <code>ResponseWorker</code> is responsible to process responses for all
 * the {@link JsonRpcClient} and it is produced by {@link ReactorFactory}.
 *
 */
public final class ResponseWorker extends Thread {
    private final LinkedBlockingQueue<MessageContext> queue;
    private static Log log = LogFactory.getLog(ResponseWorker.class);

    public ResponseWorker() {
        this.queue = new LinkedBlockingQueue<>();
        setName("ResponseWorker");
        setDaemon(true);
        start();
    }

    /**
     * Registers new client with <code>ResponseWorker</code>.
     * @param client - {@link JsonRpcClient} to be registered.
     * @return Client wrapper.
     */
    public JsonRpcClient register(ReactorClient client) {
        final JsonRpcClient jsonRpcClient = new JsonRpcClient(client);
        client.addEventListener(new MessageListener() {

            @Override
            public void onMessageReceived(byte[] message) {
                queue.add(new MessageContext(jsonRpcClient, message));
            }
        });
        return jsonRpcClient;
    }

    public void run() {
        MessageContext context = null;
        ObjectMapper mapper = new ObjectMapper();
        while (true) {
            try {
                context = this.queue.take();
                if (context.getClient() == null) {
                    break;
                }
                log.info("Message received :" + new String(context.getMessage(), UTF8));
                JsonNode rootNode = mapper.readTree(context.getMessage());
                if (!rootNode.isArray()) {
                    processIncomingObject(context.getClient(), rootNode);
                } else {
                    final Iterator<JsonNode> iter = rootNode.getElements();
                    while (iter.hasNext()) {
                        final JsonNode node = iter.next();
                        processIncomingObject(context.getClient(), node);
                    }
                }
            } catch (Exception e) {
                log.warn("Exception thrown during message processing", e);
                continue;
            }
        }
    }

    private void processIncomingObject(JsonRpcClient client, JsonNode node) {
        final JsonRpcResponse response;
        try {
            response = JsonRpcResponse.fromJsonNode(node);
        } catch (IllegalArgumentException e) {
            log.error("Recieved response is not correct", e);
            return;
        }
        client.processResponse(response);
    }

    public void close() {
        this.queue.add(new MessageContext(null, null));
    }

}
