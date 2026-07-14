/**
 * export.js — Handles rendering and downloading the final clips.
 */

import * as api from '../api.js';

export function render() {
    return `
    <div class="export-page fade-in" style="display:flex;align-items:center;justify-content:center;height:100vh;background:var(--bg-base);">
        <div class="card" style="width:100%;max-width:640px;">
            <div class="card-header" style="border-bottom:1px solid var(--border);padding:24px;">
                <h2 style="margin:0;font-size:20px;">Rendering Clips</h2>
                <p style="color:var(--text-muted);font-size:13px;margin:4px 0 0;">Processing and exporting your selected videos.</p>
            </div>
            
            <div id="export-list" style="padding:24px;display:flex;flex-direction:column;gap:16px;">
                <!-- Renders here -->
            </div>
            
            <div class="card-footer" style="padding:24px;border-top:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;background:var(--bg-elev-1);">
                <button class="btn btn-ghost" id="export-back-btn">← Back to Editor</button>
                <button class="btn btn-primary" id="export-done-btn" style="display:none;">Done</button>
            </div>
        </div>
    </div>
    `;
}

export async function mount(go) {
    const videoId = window.__app.APP.videoId;
    if (!videoId) {
        go('HOME');
        return;
    }

    const listEl = document.getElementById('export-list');
    const backBtn = document.getElementById('export-back-btn');
    const doneBtn = document.getElementById('export-done-btn');

    backBtn.addEventListener('click', () => go('BUILD_CLIPS', { mode: window.__app.APP.mode || 'manual' }));
    doneBtn.addEventListener('click', () => go('HOME'));

    let pollInterval = null;
    let renders = [];

    try {
        const clips = await api.getClips(videoId);
        const approvedClips = clips.filter(c => c.status === 'approved' || c.status === 'trimmed');
        
        if (approvedClips.length === 0) {
            listEl.innerHTML = `<div style="text-align:center;color:var(--text-dim);">No clips selected to render.</div>`;
            return;
        }

        // Start render jobs
        for (const clip of approvedClips) {
            try {
                await api.renderClip(clip.id);
            } catch (err) {
                console.error('Failed to trigger render for', clip.id, err);
            }
        }

        // Setup UI list
        renders = approvedClips.map(clip => ({
            clip,
            el: null,
            progressBar: null,
            statusText: null,
            actionContainer: null,
            done: false
        }));

        listEl.innerHTML = '';
        renders.forEach(r => {
            const title = (r.clip.title || r.clip.suggested_title || 'Untitled Clip').replace(/</g, '&lt;');
            
            const item = document.createElement('div');
            item.className = 'card';
            item.style.background = 'var(--bg-elev-2)';
            item.style.border = '1px solid var(--border)';
            item.style.padding = '16px';
            
            item.innerHTML = `
                <div style="display:flex;justify-content:space-between;margin-bottom:12px;">
                    <div style="font-weight:600;font-size:14px;">${title}</div>
                    <div class="status-text" style="font-size:12px;color:var(--text-muted);font-family:var(--font-mono);">Queued</div>
                </div>
                <div class="progress-track" style="margin-bottom:16px;">
                    <div class="progress-fill" style="width:0%;"></div>
                </div>
                <div class="action-container" style="display:none;gap:8px;">
                    <!-- buttons injected here on complete -->
                </div>
            `;
            
            r.el = item;
            r.progressBar = item.querySelector('.progress-fill');
            r.statusText = item.querySelector('.status-text');
            r.actionContainer = item.querySelector('.action-container');
            
            listEl.appendChild(item);
        });

        startPolling();
    } catch (err) {
        alert('Failed to initialize exports: ' + err.message);
    }

    async function poll() {
        try {
            const jobs = await api.getJobs(videoId);
            let allDone = true;

            for (const r of renders) {
                if (r.done) continue;

                // Find the render job for this clip
                const job = jobs.filter(j => j.clip_candidate_id === r.clip.id && j.job_type === 'render')
                                .sort((a,b) => b.id - a.id)[0];
                
                if (!job) {
                    allDone = false;
                    continue; // Wait for it to appear
                }

                if (job.status === 'failed') {
                    r.done = true;
                    r.statusText.textContent = 'Failed';
                    r.statusText.style.color = 'var(--error)';
                    r.progressBar.style.background = 'var(--error)';
                    continue;
                }

                const p = Math.max(5, Math.min(100, Math.round((job.progress || 0) * 100)));
                r.progressBar.style.width = p + '%';
                
                if (job.status === 'completed' || job.status === 'done') {
                    r.done = true;
                    r.progressBar.style.width = '100%';
                    r.statusText.textContent = 'Complete';
                    r.statusText.style.color = 'var(--success)';
                    
                    // Fetch export link
                    try {
                        const exports = await api.getExports(r.clip.id);
                        if (exports && exports.length > 0) {
                            r.actionContainer.style.display = 'flex';
                            const path = exports[0].file_path;
                            // the file_path is an absolute path on server.
                            // The backend serves it via static mount if it's in /static/output/
                            // Actually backend doesn't mount /outputs natively in Sprint 1 unless we added it?
                            // Wait, main.py might mount it. Or we can just use the absolute path for a "copy link" feature.
                            const filename = path.split(/[/\\]/).pop();
                            
                            r.actionContainer.innerHTML = `
                                <a href="/static/output/${filename}" target="_blank" download class="btn btn-secondary btn-sm" style="font-size:12px;padding:6px 12px;">⬇ Download</a>
                            `;
                        }
                    } catch (e) {
                        console.error('Failed to get export data', e);
                    }
                } else {
                    allDone = false;
                    r.statusText.textContent = `Rendering ${p}%`;
                }
            }

            if (allDone && renders.length > 0) {
                clearInterval(pollInterval);
                backBtn.style.display = 'none';
                doneBtn.style.display = 'block';
            }
            
        } catch (err) {
            console.error('Polling error:', err);
        }
    }

    function startPolling() {
        if (pollInterval) clearInterval(pollInterval);
        pollInterval = setInterval(poll, 2000);
        poll();
    }

    // Cleanup
    const originalGo = window.__app.go;
    window.__app.go = (...args) => {
        if (pollInterval) clearInterval(pollInterval);
        window.__app.go = originalGo;
        originalGo(...args);
    };
}
