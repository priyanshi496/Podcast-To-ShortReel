/**
 * state.js — Entry point and state machine for ShortReel.
 *
 * APP holds ALL state. Server state (ids) and UI state are
 * explicitly separated by naming convention:
 *   - Server state:  projectId, videoId, jobId
 *   - UI state:      state, mode, editor.*, overlays.*
 */

import { renderWizard } from './components/wizard.js';
import * as storage from './storage.js';
import * as homePage from './pages/home.js';
import * as uploadPage from './pages/upload.js';
import * as transcribingPage from './pages/transcribing.js';
import * as readyPage from './pages/ready.js';
import * as chooseModePage from './pages/choose-mode.js';
import * as buildClipsPage from './pages/build-clips.js';
import * as exportPage from './pages/export.js';

// ── App State Object ──────────────────────────────────────────────────────────

export const APP = {
    // ── Server state refs (persisted) ──────────────────────────────────
    projectId: null,
    videoId:   null,
    jobId:     null,

    // ── Navigation state (persisted) ───────────────────────────────────
    state: 'HOME',       // HOME | UPLOAD | TRANSCRIBING | READY | CHOOSE_MODE | BUILD_CLIPS
    mode:  null,         // 'manual' | 'ai'

    // ── Editor UI state (NOT persisted — re-derived from server) ───────
    editor: {
        currentTime:     0,
        inPoint:         0,
        outPoint:        30,
        transcriptQuery: '',
        aiSuggestions:   [],
        selectedClips:   [],
    },

    // ── Overlay/drawer UI state (NOT persisted) ─────────────────────────
    overlays: {
        aiRanking: false,
        render:    false,
        export:    false,
        cmdbar:    false,
    },
};

// ── Page Registry ─────────────────────────────────────────────────────────────

/** Each page module must export: render() → HTMLString, mount(go) → void */
const PAGES = {
    HOME:        homePage,
    UPLOAD:      uploadPage,
    TRANSCRIBING:transcribingPage,
    READY:       readyPage,
    CHOOSE_MODE: chooseModePage,
    BUILD_CLIPS: buildClipsPage,
    EXPORT:      exportPage,
};

// ── State Transition ──────────────────────────────────────────────────────────

/**
 * go(newState, patch) — Transition to a new state.
 * patch: optional object merged into APP before rendering.
 * Every important action calls go() which auto-saves.
 */
export function go(newState, patch = {}) {
    Object.assign(APP, patch);
    APP.state = newState;

    // Save navigational state (not ephemeral editor state)
    storage.save(APP);

    // Re-render the active page and wizard
    _render();
}

// ── Render ────────────────────────────────────────────────────────────────────

function _render() {
    // Update wizard
    renderWizard(APP.state, () => go('HOME'));

    // Mount correct page
    const appEl = document.getElementById('app');
    if (!appEl) return;

    const page = PAGES[APP.state];

    if (!page) {
        // Sprint stubs: show a placeholder for pages not yet implemented
        appEl.innerHTML = `
            <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:60vh;gap:16px;opacity:0.5;">
                <div style="font-size:48px;">🚧</div>
                <h2 style="color:var(--text-muted);font-weight:500;">
                    ${APP.state} — Coming in the next sprint
                </h2>
                <button class="btn btn-ghost" onclick="window.__app.go('HOME')">← Back to Home</button>
            </div>
        `;
        return;
    }

    appEl.innerHTML = page.render();
    appEl.classList.remove('fade-in');
    void appEl.offsetWidth; // force reflow for animation reset
    appEl.classList.add('fade-in');

    page.mount?.(go);
}

// ── Keyboard Shortcuts ────────────────────────────────────────────────────────

function _initKeyboard() {
    document.addEventListener('keydown', (e) => {
        // ⌘K / Ctrl+K — command bar (Sprint 7)
        if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
            e.preventDefault();
            APP.overlays.cmdbar = !APP.overlays.cmdbar;
            // TODO: render cmdbar in Sprint 7
        }
    });
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────

function _boot() {
    // Expose APP + go() globally for debugging
    window.__app = { APP, go };

    // Try to rehydrate from localStorage
    const saved = storage.load();
    if (saved?.state && saved.state !== 'HOME') {
        Object.assign(APP, {
            projectId: saved.projectId ?? null,
            videoId:   saved.videoId ?? null,
            state:     saved.state,
            mode:      saved.mode ?? null,
        });
    }

    // Start autosave
    storage.startAutosave(() => APP);

    // Init keyboard shortcuts
    _initKeyboard();

    // Initial render
    _render();
}

// Run on DOM ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _boot);
} else {
    _boot();
}
