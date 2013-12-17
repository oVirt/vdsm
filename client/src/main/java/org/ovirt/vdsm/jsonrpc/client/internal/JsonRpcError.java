package org.ovirt.vdsm.jsonrpc.client.internal;

import org.codehaus.jackson.JsonNode;

/**
 * Java bean representing error information from json message.
 *
 */
public final class JsonRpcError {
    private int code;
    private String message;
    private JsonNode data;

    public JsonRpcError(int code, String message, JsonNode data) {
        this.code = code;
        this.message = message;
        this.data = data;
    }

    public int getCode() {
        return this.code;
    }

    public void setCode(int code) {
        this.code = code;
    }

    public String getMessage() {
        return this.message;
    }

    public void setMessage(String message) {
        this.message = message;
    }

    public JsonNode getData() {
        return this.data;
    }

    public void setData(JsonNode data) {
        this.data = data;
    }

    /**
     * Creates {@link JsonRpcError} representation from
     * provided {@link JsonNode} message.
     *
     * @param node Object representing message.
     * @return Error information.
     */
    public static JsonRpcError fromJson(JsonNode node) {
        JsonNode tmp = node.get("code");
        int code = -1;
        if (tmp.isInt()) {
            code = tmp.asInt();
        }

        String message = null;
        tmp = node.get("message");
        if (tmp != null) {
            message = tmp.asText();
        }

        final JsonNode data = node.get("data");

        return new JsonRpcError(code, message, data);
    }

}
