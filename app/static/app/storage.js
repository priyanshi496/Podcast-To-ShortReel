/**
 * storage.js — Persist and rehydrate UI state from localStorage.
 * Server state (DB records) is always re-fetched from the backend.
 * Only UI navigation state is persisted here.
 */

const KEY = 'shortreel_state_v1';

/** Fields that are safe to persist across sessions. */
const PERSIST_FIELDS = [
    'projectId',
    'videoId',
    'state',
    'mode',
];

/** Save current navigation state to localStorage. */
export function save(app) {
    try {
        const payload = {
            savedAt: Date.now(),
        };
        for (const field of PERSIST_FIELDS) {
            payload[field] = app[field];
        }
        localStorage.setItem(KEY, JSON.stringify(payload));
    } catch (e) {
        console.warn('[storage] save failed:', e);
    }
}

/** Load persisted state. Returns null if nothing saved or stale (>24h). */
export function load() {
    try {
        const raw = localStorage.getItem(KEY);
        if (!raw) return null;
        const data = JSON.parse(raw);
        // Discard state older than 24 hours
        if (Date.now() - (data.savedAt || 0) > 86_400_000) {
            clear();
            return null;
        }
        return data;
    } catch (e) {
        return null;
    }
}

/** Clear all persisted state (called on "Start New Project"). */
export function clear() {
    localStorage.removeItem(KEY);
}

/** Set up autosave interval (every 5 seconds). */
export function startAutosave(getAppFn) {
    return setInterval(() => {
        try { save(getAppFn()); } catch (_) { /* silent */ }
    }, 5000);
}
