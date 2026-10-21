package com.aion.chat;

import java.util.ArrayDeque;

/** Main-thread speech ownership shared by the WebView and push service players. */
final class TtsPlaybackCoordinator {
    static final TtsPlaybackCoordinator shared = new TtsPlaybackCoordinator();

    private static final class Pending {
        final Object owner;
        final Runnable start;
        Pending(Object owner, Runnable start) { this.owner = owner; this.start = start; }
    }

    private Object active;
    private final ArrayDeque<Pending> pending = new ArrayDeque<>();

    void request(Object owner, Runnable start) {
        if (active == owner) {
            start.run(); // Continue the same message without yielding between its clips.
            return;
        }
        for (Pending item : pending) if (item.owner == owner) return;
        pending.addLast(new Pending(owner, start));
        startNext();
    }

    void release(Object owner) {
        pending.removeIf(item -> item.owner == owner);
        if (active == owner) active = null;
        startNext();
    }

    private void startNext() {
        if (active != null || pending.isEmpty()) return;
        Pending item = pending.removeFirst();
        active = item.owner;
        item.start.run();
    }
}
