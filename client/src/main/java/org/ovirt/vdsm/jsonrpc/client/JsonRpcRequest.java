package org.ovirt.vdsm.jsonrpc.client;

import java.io.IOException;

import org.codehaus.jackson.JsonNode;
import org.codehaus.jackson.map.ObjectMapper;
import org.codehaus.jackson.node.ObjectNode;

/**
 * Java bean representation of the request.
 *
 */
public class JsonRpcRequest {
    private final static ObjectMapper MAPPER = new ObjectMapper();
    private String method;
    private JsonNode params;
    private JsonNode id;

    /**
     * Creates request object.
     * @param method - Name of the method which will be executed remotely.
     * @param params - Parameters used to execute method.
     * @param id - Unique identifier of the message.
     */
    public JsonRpcRequest(String method, JsonNode params, JsonNode id) {
        this.method = method;
        this.params = params;
        this.id = id;
    }

    public String getMethod() {
        return this.method;
    }

    public void setMethod(String method) {
        this.method = method;
    }

    public JsonNode getParams() {
        return this.params;
    }

    public void setParams(JsonNode node) {
        this.params = node;
    }

    public JsonNode getId() {
        return this.id;
    }

    public void setId(JsonNode node) {
        this.id = node;
    }

    public String getPlainId() {
        return getId().getTextValue();
    }

    /**
     * Validates and builds {@link JsonRpcRequest} based on provided json node.
     * @param node - Json representation of the request.
     * @return Request object.
     */
    public static JsonRpcRequest fromJsonNode(JsonNode node) {
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

        return new JsonRpcRequest(method, node.get("params"), node.get("id"));
    }

    /**
     * @see JsonRpcRequest#fromJsonNode(JsonNode)
     *
     * @param message - byte array representation of the request.
     * @return Request object.
     */
    public static JsonRpcRequest fromByteArray(byte[] message) {
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
        if (getId() == null) {
            node.putNull("id");
        } else {
            node.put("id", getId());
        }
        return node;
    }

    @Override
    public String toString() {
        return "<JsonRpcRequest id: " + this.getId() + ", method: " + this.getMethod() + ", params: " + this.getParams().toString() +  ">";
    }
}
