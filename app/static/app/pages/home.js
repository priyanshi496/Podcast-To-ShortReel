/**
 * home.js — Home screen with "New Project" CTA and Recent Projects grid.
 */

import * as api from '../api.js';

function timeAgo(dateStr) {
    const ms = Date.now() - new Date(dateStr).getTime();
    const mins = Math.floor(ms / 60_000);
    if (mins < 1)  return 'Just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24)  return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days < 7)  return `${days}d ago`;
    return new Date(dateStr).toLocaleDateString();
}

function projectCard(project, onOpen, onDelete) {
    const card = document.createElement('div');
    card.className = 'home-project-card';
    card.setAttribute('role', 'button');
    card.setAttribute('tabindex', '0');
    card.setAttribute('aria-label', `Open project: ${project.name}`);

    card.innerHTML = `
        <div class="home-project-icon">🎙</div>
        <div class="home-project-body">
            <div class="home-project-name">${project.name}</div>
            <div class="home-project-meta">
                ${project.video_count} video${project.video_count !== 1 ? 's' : ''}
                &nbsp;·&nbsp;
                ${project.clip_count} clip${project.clip_count !== 1 ? 's' : ''}
            </div>
            <div class="home-project-time">${timeAgo(project.updated_at)}</div>
        </div>
        <div class="home-project-actions">
            <button class="btn btn-ghost home-project-delete" data-id="${project.id}" title="Delete project" aria-label="Delete project">✕</button>
            <button class="btn btn-secondary home-project-open" data-id="${project.id}" style="font-size:13px;padding:6px 14px;">Open →</button>
        </div>
    `;

    card.querySelector('.home-project-open').addEventListener('click', (e) => {
        e.stopPropagation();
        onOpen(project);
    });
    card.querySelector('.home-project-delete').addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm(`Delete project "${project.name}"? This cannot be undone.`)) return;
        await onDelete(project.id, card);
    });
    card.addEventListener('click', () => onOpen(project));
    card.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') onOpen(project); });

    return card;
}

export function render() {
    return `
    <div class="home-page fade-in">
        <!-- Hero -->
        <div class="home-hero">
            <div class="home-hero-inner">
                <div class="home-hero-badge">✨ Podcast → Short Reels</div>
                <h1>Turn long podcasts into<br>viral short clips</h1>
                <p>AI-powered clip extraction, manual editing, and one-click export.<br>Your podcast studio, reimagined.</p>
                <button class="btn btn-primary home-new-btn" id="home-new-project" style="font-size:15px;padding:12px 28px;margin-top:8px;">
                    + New Project
                </button>
            </div>
        </div>

        <!-- Recent Projects -->
        <div class="home-projects-section">
            <div class="home-projects-header">
                <h2>Recent Projects</h2>
            </div>
            <div class="home-projects-grid" id="home-projects-grid">
                <!-- Skeleton loaders -->
                ${[1,2,3].map(() => `<div class="home-project-card skeleton" style="height:96px;"></div>`).join('')}
            </div>
        </div>
    </div>
    `;
}

export async function mount(go) {
    // Wire "New Project" button
    document.getElementById('home-new-project')?.addEventListener('click', () => {
        go('UPLOAD', {});
    });

    // Load recent projects
    const grid = document.getElementById('home-projects-grid');
    if (!grid) return;

    try {
        const projects = await api.getProjects();

        if (projects.length === 0) {
            grid.innerHTML = `
                <div class="empty-state" style="grid-column:1/-1;padding:48px 24px;">
                    <div class="empty-state-icon">📂</div>
                    <h3>No projects yet</h3>
                    <p>Create your first project to get started.</p>
                </div>
            `;
            return;
        }

        grid.innerHTML = '';
        for (const project of projects) {
            const card = projectCard(
                project,
                (p) => go('UPLOAD', { projectId: p.id }),
                async (id, cardEl) => {
                    try {
                        await api.deleteProject(id);
                        cardEl.style.transition = 'opacity 200ms, transform 200ms';
                        cardEl.style.opacity = '0';
                        cardEl.style.transform = 'scale(0.95)';
                        setTimeout(() => cardEl.remove(), 220);
                    } catch (err) {
                        alert(`Could not delete: ${err.message}`);
                    }
                }
            );
            grid.appendChild(card);
        }
    } catch (err) {
        grid.innerHTML = `
            <div class="empty-state" style="grid-column:1/-1;">
                <div class="empty-state-icon">⚠️</div>
                <h3>Failed to load projects</h3>
                <p>${err.message}</p>
                <button class="btn btn-secondary" onclick="location.reload()">Retry</button>
            </div>
        `;
    }
}
