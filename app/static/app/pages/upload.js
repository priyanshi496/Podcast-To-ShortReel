/**
 * upload.js — Handles video upload (MP4 drop/select) and YouTube URL ingestion.
 */

import * as api from '../api.js';

export function render() {
    return `
    <div class="upload-page fade-in">
        <div class="upload-container">
            <h1 style="text-align:center;margin-bottom:8px;">Add Video</h1>
            <p style="text-align:center;margin-bottom:32px;">Upload an MP4 or paste a YouTube link.</p>

            <div class="card">
                <div class="card-body">
                    <!-- Drop Zone -->
                    <div class="upload-dropzone" id="upload-dropzone" tabindex="0" role="button">
                        <div class="upload-dropzone-icon">📁</div>
                        <h3>Click or drag video here</h3>
                        <p>MP4, MOV up to 2GB</p>
                        <input type="file" id="upload-file-input" accept="video/mp4,video/quicktime" style="display:none;">
                    </div>

                    <div class="upload-divider">
                        <span>OR</span>
                    </div>

                    <!-- YouTube Input -->
                    <div class="upload-youtube">
                        <input type="url" id="upload-yt-input" class="input" placeholder="Paste YouTube URL...">
                        <button class="btn btn-secondary" id="upload-yt-btn">Import</button>
                    </div>

                    <!-- Global upload error / progress -->
                    <div id="upload-status" class="upload-status" style="display:none;"></div>
                    
                    <!-- YouTube Format Options -->
                    <div id="yt-format-options" style="display:none;margin-top:16px;display:flex;flex-direction:column;gap:8px;"></div>
                </div>
            </div>
        </div>
    </div>
    `;
}

export function mount(go) {
    const dropzone = document.getElementById('upload-dropzone');
    const fileInput = document.getElementById('upload-file-input');
    const ytInput = document.getElementById('upload-yt-input');
    const ytBtn = document.getElementById('upload-yt-btn');
    const statusEl = document.getElementById('upload-status');
    
    // We need APP.projectId if we are adding to an existing project, or null if creating new
    const projectId = window.__app.APP.projectId;

    function setStatus(msg, isError = false) {
        statusEl.style.display = 'block';
        statusEl.className = `upload-status ${isError ? 'error' : 'info'}`;
        statusEl.textContent = msg;
    }

    async function handleFile(file) {
        if (!file) return;
        setStatus('Uploading video...');
        try {
            const result = await api.uploadVideo(file, projectId);
            if (!result.id) throw new Error('Upload failed: no id returned');
            // If we created a new project during upload (backend handles this if projectId was null), 
            // the response should ideally return project_id, but we rely on id for now.
            go('TRANSCRIBING', { videoId: result.id, projectId: result.project_id || projectId });
        } catch (err) {
            setStatus(err.message, true);
        }
    }

    // Drag & Drop events
    dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropzone.classList.add('dragover');
    });
    dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
    dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('dragover');
        handleFile(e.dataTransfer.files[0]);
    });

    // Click to select file
    dropzone.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => handleFile(fileInput.files[0]));
    dropzone.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') fileInput.click();
    });

    // YouTube Import
    ytBtn.addEventListener('click', async () => {
        const url = ytInput.value.trim();
        if (!url) return;

        setStatus('Fetching YouTube info...');
        ytBtn.disabled = true;
        ytInput.disabled = true;

        try {
            const info = await api.getYouTubeInfo(url);
            if (!info.formats || info.formats.length === 0) {
                throw new Error('No downloadable formats found');
            }
            
            // Filter to just mp4 formats
            const validFormats = info.formats.filter(f => f.ext === 'mp4' && f.resolution && f.resolution !== 'audio only');
            if (validFormats.length === 0) throw new Error('No MP4 formats found');
            
            setStatus(`Found: ${info.title}`, false);
            
            const optionsEl = document.getElementById('yt-format-options');
            optionsEl.style.display = 'flex';
            optionsEl.innerHTML = '<div style="font-size:12px;color:var(--text-muted);margin-bottom:4px;">Select resolution:</div>';
            
            validFormats.forEach(format => {
                const mb = format.filesize_approx ? (format.filesize_approx / 1024 / 1024).toFixed(1) + ' MB' : 'Unknown size';
                
                const btn = document.createElement('button');
                btn.className = 'btn btn-secondary';
                btn.style.display = 'flex';
                btn.style.justifyContent = 'space-between';
                btn.style.padding = '12px';
                btn.innerHTML = `
                    <span style="font-weight:600;">${format.resolution}</span>
                    <span style="color:var(--text-muted);font-family:var(--font-mono);">${mb}</span>
                `;
                
                btn.addEventListener('click', async () => {
                    optionsEl.style.display = 'none';
                    setStatus(`Downloading ${format.resolution}...`);
                    try {
                        const result = await api.downloadYouTubeVideo(url, format.format_id, projectId);
                        go('TRANSCRIBING', { videoId: result.id, projectId: result.project_id || projectId });
                    } catch (err) {
                        setStatus(err.message, true);
                        ytBtn.disabled = false;
                        ytInput.disabled = false;
                    }
                });
                optionsEl.appendChild(btn);
            });
            
        } catch (err) {
            setStatus(err.message, true);
            ytBtn.disabled = false;
            ytInput.disabled = false;
        }
    });

    ytInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') ytBtn.click();
    });
}
