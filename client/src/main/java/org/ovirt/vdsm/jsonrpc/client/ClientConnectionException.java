package org.ovirt.vdsm.jsonrpc.client;

/**
 * Generic json-rpc client exception which wraps IO or encryption relates exceptions.
 *
 */
public class ClientConnectionException extends Exception {

    private static final long serialVersionUID = 3882225302271019060L;

    public ClientConnectionException() {
    }

    public ClientConnectionException(String message) {
        super(message);
    }

    public ClientConnectionException(Throwable cause) {
        super(cause);
    }

    public ClientConnectionException(String message, Throwable cause) {
        super(message, cause);
    }

    public ClientConnectionException(String message, Throwable cause,
            boolean enableSuppression, boolean writableStackTrace) {
        super(message, cause, enableSuppression, writableStackTrace);
    }

    @Override
    public synchronized Throwable getCause() {
        Throwable throwable = super.getCause();
        if (throwable == null) {
            return this;
        }
        Throwable previous = throwable;
        while (throwable != null) {
            previous = throwable;
            throwable = previous.getCause();
        }
        return previous;
    }
}
