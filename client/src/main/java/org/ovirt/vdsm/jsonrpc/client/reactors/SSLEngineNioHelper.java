package org.ovirt.vdsm.jsonrpc.client.reactors;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.SocketChannel;
import java.util.EnumSet;

import javax.net.ssl.SSLEngine;
import javax.net.ssl.SSLEngineResult;
import javax.net.ssl.SSLException;
import javax.net.ssl.SSLSession;

import org.ovirt.vdsm.jsonrpc.client.utils.OneTimeCallback;

/**
 * Helper object responsible for low level ssl communication.
 *
 */
public class SSLEngineNioHelper {

    private final SocketChannel channel;
    private final SSLEngine engine;
    private final ByteBuffer appBuffer;
    private final ByteBuffer packetBuffer;
    private final ByteBuffer appPeerBuffer;
    private final ByteBuffer packatPeerBuffer;
    private OneTimeCallback callback;

    public SSLEngineNioHelper(SocketChannel channel, SSLEngine engine, OneTimeCallback callback) {
        this.channel = channel;
        this.engine = engine;
        this.callback = callback;
        SSLSession session = engine.getSession();

        this.appBuffer = ByteBuffer.allocate(session.getApplicationBufferSize());
        this.packetBuffer = ByteBuffer.allocate(session.getPacketBufferSize());
        this.appPeerBuffer = ByteBuffer.allocate(session.getApplicationBufferSize());
        this.packatPeerBuffer = ByteBuffer.allocate(session.getPacketBufferSize());
    }

    public void beginHandshake() throws SSLException {
        this.engine.beginHandshake();
    }

    public int read(ByteBuffer buff) throws IOException {
        int read = 0;
        if (this.appPeerBuffer.position() < buff.limit()) {
            this.channel.read(this.packatPeerBuffer);

            this.packatPeerBuffer.flip();

            SSLEngineResult result = this.engine.unwrap(this.packatPeerBuffer, this.appPeerBuffer);
            read = result.bytesProduced();
            this.packatPeerBuffer.compact();
        }
        this.appPeerBuffer.flip();
        final ByteBuffer slice = this.appPeerBuffer.slice();
        if (slice.limit() > buff.remaining()) {
            slice.limit(buff.remaining());
        }

        buff.put(slice);
        this.appPeerBuffer.position(this.appPeerBuffer.position() + slice.limit());
        this.appPeerBuffer.compact();
        return read;
    }

    public void write(ByteBuffer buff) throws IOException {
        if (buff != this.appBuffer) {
            this.appBuffer.put(buff);
        }
        this.appBuffer.flip();
        this.engine.wrap(this.appBuffer, this.packetBuffer);
        this.appBuffer.compact();

        this.packetBuffer.flip();
        this.channel.write(this.packetBuffer);
        this.packetBuffer.compact();

    }

    @SuppressWarnings("incomplete-switch")
    public Runnable process() throws IOException {
        if (!handshakeInProgress()) {
            if (this.callback != null) {
                this.callback.checkAndExecute();
            }
            return null;
        }

        final SSLEngineResult.HandshakeStatus hs = this.engine.getHandshakeStatus();
        switch (hs) {
        case NEED_UNWRAP:
            this.read(appPeerBuffer);
            return null;
        case NEED_WRAP:
            this.write(appBuffer);
            return null;
        case NEED_TASK:
            return engine.getDelegatedTask();
        }
        return null;
    }

    boolean handshakeInProgress() {
        final SSLEngineResult.HandshakeStatus hs = this.engine.getHandshakeStatus();

        final EnumSet<SSLEngineResult.HandshakeStatus> handshakeEndStates =
                EnumSet.of(SSLEngineResult.HandshakeStatus.FINISHED,
                        SSLEngineResult.HandshakeStatus.NOT_HANDSHAKING);

        return !handshakeEndStates.contains(hs);
    }
}
