package com.aion.chat;

import android.content.Context;
import android.content.ContextWrapper;
import android.media.MediaPlayer;
import android.webkit.ValueCallback;
import android.webkit.WebView;
import java.lang.reflect.Field;
import java.util.Map;
import java.util.Set;
import org.junit.Test;
import static org.junit.Assert.*;

public class TtsAudioBridgeTest {
    private static final class PreparedPlayer extends MediaPlayer {
        boolean playing, released;
        @Override public boolean isPlaying() { return playing; }
        @Override public void start() { playing = true; }
        @Override public void pause() {
            if (!playing) throw new IllegalStateException("Cannot pause a prepared player");
            playing = false;
        }
        @Override public void release() { released = true; }
    }

    @Test @SuppressWarnings("unchecked")
    public void pausingReadingQueuedBehindChatKeepsItsUnplayedSegment() throws Exception {
        Context context = new ContextWrapper(null) {
            @Override public Context getApplicationContext() { return this; }
        };
        WebView webView = new WebView(context) {
            @Override public boolean post(Runnable action) { action.run(); return true; }
            @Override public void evaluateJavascript(String script, ValueCallback<String> callback) {}
        };
        TtsAudioBridge bridge = new TtsAudioBridge(context, webView);
        PreparedPlayer player = new PreparedPlayer();
        Field players = TtsAudioBridge.class.getDeclaredField("players");
        players.setAccessible(true);
        ((Map<String, MediaPlayer>) players.get(bridge)).put("tts-reading", player);
        Field prepared = TtsAudioBridge.class.getDeclaredField("preparedPlayers");
        prepared.setAccessible(true);
        ((Set<String>) prepared.get(bridge)).add("tts-reading");
        Object chat = new Object();
        try {
            TtsPlaybackCoordinator.shared.request(chat, () -> {});
            bridge.resumeAudio("tts-reading");
            bridge.pauseAudio("tts-reading");
            assertFalse("Pausing queued reading must not discard it as an error", player.released);
            TtsPlaybackCoordinator.shared.release(chat);
            assertFalse("Paused reading must no longer be queued", player.playing);
            bridge.resumeAudio("tts-reading");
            assertTrue(player.playing);
            bridge.pauseAudio("tts-reading");
            assertFalse(player.playing);
        } finally {
            bridge.shutdown();
            TtsPlaybackCoordinator.shared.release(chat);
        }
    }
}
