/**
 * api.js — All fetch() wrappers for the ShortReel backend.
 * All functions are async and throw on non-2xx responses.
 */

const BASE = '';

async function _fetch(path, opts = {}) {
    const r = await fetch(`${BASE}${path}`, {
        headers: { 'Content-Type': 'application/json', ...opts.headers },
        ...opts,
    });
    if (!r.ok) {
        const msg = await r.text().catch(() => r.statusText);
        throw new Error(`${r.status} ${msg}`);
    }
    if (r.status === 204) return null;
    return r.json();
}

// ── Projects ──────────────────────────────────────────────────────────────────
export const getProjects   = ()          => _fetch('/projects');
export const createProject = (name, description = null) =>
    _fetch('/projects', { method: 'POST', body: JSON.stringify({ name, description }) });
export const getProject    = (id)        => _fetch(`/projects/${id}`);
export const renameProject = (id, name)  =>
    _fetch(`/projects/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) });
export const deleteProject = (id)        =>
    _fetch(`/projects/${id}`, { method: 'DELETE' });

// ── Videos ───────────────────────────────────────────────────────────────────
export const getVideo      = (id)        => _fetch(`/videos/${id}`);
export const getVideos     = ()          => _fetch('/videos');
export const deleteVideo   = (id)        =>
    _fetch(`/videos/${id}`, { method: 'DELETE' });

export async function uploadVideo(file, projectId) {
    const form = new FormData();
    form.append('file', file);
    const url = projectId ? `/videos/upload?project_id=${projectId}` : '/videos/upload';
    const r = await fetch(url, { method: 'POST', body: form });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return r.json();
}

export const getYouTubeInfo      = (url)              =>
    _fetch('/videos/youtube/info', { method: 'POST', body: JSON.stringify({ url }) });
export const downloadYouTubeVideo = (url, formatId, projectId) =>
    _fetch(`/videos/youtube/download${projectId ? `?project_id=${projectId}` : ''}`, {
        method: 'POST',
        body: JSON.stringify({ url, format_id: formatId }),
    });

export const processVideo  = (id)        =>
    _fetch(`/videos/${id}/process`, { method: 'POST' });
export const rankVideo     = (id)        =>
    _fetch(`/videos/${id}/rank`, { method: 'POST' });
export const getVideoStatus = (id)       => _fetch(`/videos/${id}/status`);

// ── Jobs ──────────────────────────────────────────────────────────────────────
export const getJobs = (videoId)         => _fetch(`/videos/${videoId}/jobs`);

// ── Transcript ────────────────────────────────────────────────────────────────
export const getTranscript = (videoId)   => _fetch(`/videos/${videoId}/transcript`);

// ── Clips ─────────────────────────────────────────────────────────────────────
export const getClips      = (videoId)   => _fetch(`/videos/${videoId}/clips`);

export const createManualClip = (videoId, startTime, endTime, title = null) =>
    _fetch(`/videos/${videoId}/clips`, {
        method: 'POST',
        body: JSON.stringify({ start_time: startTime, end_time: endTime, title }),
    });

export const approveClip   = (clipId)    =>
    _fetch(`/clips/${clipId}/approve`, { method: 'POST' });
export const rejectClip    = (clipId)    =>
    _fetch(`/clips/${clipId}/reject`, { method: 'POST' });
export const trimClip      = (clipId, startTime, endTime) =>
    _fetch(`/clips/${clipId}/trim`, { method: 'POST', body: JSON.stringify({ start_time: startTime, end_time: endTime }) });
export const renderClip    = (clipId)    =>
    _fetch(`/clips/${clipId}/render`, { method: 'POST' });
export const getExports    = (clipId)    =>
    _fetch(`/clips/${clipId}/exports`);

// ── Thumbnail ─────────────────────────────────────────────────────────────────
export const thumbnailUrl  = (videoId, t) => `/videos/${videoId}/thumbnail?t=${Math.floor(t)}`;
