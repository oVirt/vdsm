package org.ovirt.vdsm.jsonrpc.client;

import java.io.IOException;
import java.util.Map;

import org.codehaus.jackson.JsonNode;
import org.codehaus.jackson.map.ObjectMapper;
import org.codehaus.jackson.node.ObjectNode;
import org.ovirt.vdsm.jsonrpc.client.utils.JsonUtils;

/**
 * Java bean representation of an event.
 *
 */
public class JsonRpcEvent {
    public final static String ERROR_KEY = "communicationError";
    private final static ObjectMapper MAPPER = new ObjectMapper();
    public final static String MESSAGE_FORMAT =
            "{\"jsonrpc\": \"2.0\", \"method\": \"%s\", \"params\": %s}";
    private String method;
    private JsonNode params;

    /**
     * Creates notification object.
     *
     * @param method - Name of the method which will be executed remotely.
     * @param params - Parameters used to execute method.
     */
    public JsonRpcEvent(String method, JsonNode params) {
        this.method = method;
        this.params = params;
    }

    /**
     * @return Id of this event which is used to match a subscriber.
     */
    public String getMethod() {
        return this.method;
    }

    public void setMethod(String method) {
        this.method = method;
    }

    /**
     * @return Content of an event.
     */
    public JsonNode getParams() {
        return this.params;
    }

    public void setParams(JsonNode node) {
        this.params = node;
    }

    /**
     * Validates and builds {@link JsonRpcEvent} based on provided json node.
     *
     * @param node - Json representation of the notification.
     * @return Notification object.
     */
    public static JsonRpcEvent fromJsonNode(JsonNode node) {
        JsonNode tmp = node.get("jsonrpc");
        if (tmp == null) {
            throw new IllegalArgumentException(
                    "'jsonrpc' field missing in node");
        }

        String version = tmp.asText();
        if (version == null || !version.equals("2.0")) {
            throw new IllegalArgumentException("Only jsonrpc 2.0 is supported");
        }

        tmp = node.get("method");
        if (tmp == null) {
            throw new IllegalArgumentException("'method' field missing in node");
        }

        if (!tmp.isTextual()) {
            throw new IllegalArgumentException("'method' field is not textual");
        }

        String method = tmp.asText();
        if (method.isEmpty()) {
            throw new IllegalArgumentException(
                    "'method' field contains an empty string");
        }

        return new JsonRpcEvent(method, node.get("params"));
    }

    /**
     * Create notification object for the method and params.
     *
     * @param method - Name of the method which will be executed remotely.
     * @param params - Parameters used to execute method.
     * @return <code>JsonRpcEvent</code> representing an event.
     * @throws IOException - The exception thrown if params cannot be serialized.
     */
    public static JsonRpcEvent fromMethodAndParams(String method, Map<String, Object> params) throws IOException {
        return fromByteArray(String.format(MESSAGE_FORMAT, method, MAPPER.writeValueAsString(params))
                .getBytes(JsonUtils.UTF8));
    }

    /**
     * @see JsonRpcEvent#fromJsonNode(JsonNode)
     *
     * @param message - byte array representation of the notification.
     * @return Request object.
     */
    public static JsonRpcEvent fromByteArray(byte[] message) {
        try {
            return fromJsonNode(MAPPER.readTree(message));
        } catch (IOException e) {
            return null;
        }
    }

    /**
     * @return Content of this bean as {@link JsonNode}.
     */
    public JsonNode toJson() {
        ObjectNode node = MAPPER.createObjectNode();
        node.put("jsonrpc", "2.0");
        if (getMethod() == null) {
            node.putNull("method");
        } else {
            node.put("method", getMethod());
        }
        if (getParams() == null) {
            node.putNull("params");
        } else {
            node.put("params", getParams());
        }
        return node;
    }

    @Override
    public String toString() {
        return "<JsonRpcEvent method: " + this.getMethod() + ", params: " + this.getParams().toString() + ">";
    }
}
