package org.ovirt.vdsm.jsonrpc.server;

import static junit.framework.Assert.fail;
import static org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils.jsonToByteArray;

import java.nio.channels.SocketChannel;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.TimeUnit;

import org.codehaus.jackson.JsonNode;
import org.codehaus.jackson.map.ObjectMapper;
import org.codehaus.jackson.node.ArrayNode;
import org.codehaus.jackson.node.ObjectNode;

public class Worker extends Thread {
    private final static int TIMEOUT_SEC = 300;
    private BlockingQueue<ServerDataEvent> queue = new ArrayBlockingQueue<>(1);
    private Map<String, Processor> responses = new HashMap<>();
    private static ObjectMapper mapper = new ObjectMapper();

    public Worker() {
        this.responses.put("echo", new Processor() {

            @Override
            public JsonNode process(JsonNode node) {
                ObjectNode result = mapper.createObjectNode();
                result.put("jsonrpc", node.get("jsonrpc"));
                result.put("id", node.get("id"));
                result.put("result", node.get("params").get("text").asText());
                return result;
            }

        });
        this.responses.put("ping", new Processor() {

            @Override
            public JsonNode process(JsonNode node) {
                return node;
            }
        });
    }

    public void processData(JsonRpcServer server, SocketChannel socket, byte[] data) {
        this.queue.add(new ServerDataEvent(server, socket, data));
    }

    public void run() {
        ServerDataEvent dataEvent;
        while (true) {
            try {
                dataEvent = (ServerDataEvent) this.queue.poll(TIMEOUT_SEC, TimeUnit.SECONDS);
                JsonNode rootNode = mapper.readTree(dataEvent.data);
                JsonNode result = null;
                if (rootNode.isArray()) {
                    ArrayNode array = mapper.createObjectNode().arrayNode();
                    for (JsonNode singleNode : rootNode) {
                        array.add(process(singleNode));
                    }
                    result = (JsonNode) array;
                } else {
                    result = process(rootNode);
                }
                dataEvent.server.send(dataEvent.socket, jsonToByteArray(result));
            } catch (Exception e) {
                fail();
            }
        }
    }

    private JsonNode process(JsonNode node) {
        JsonNode result = mapper.createObjectNode();
        Processor processor = this.responses.get(node.get("method").asText());
        if (processor != null) {
            result = processor.process(node);
        }
        return result;
    }

    interface Processor {
        JsonNode process(JsonNode node);
    }
}
