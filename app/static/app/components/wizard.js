/**
 * wizard.js — Top Navigation and Workflow Breadcrumbs
 */

const STEPS = [
    { id: 'UPLOAD',      label: 'Upload' },
    { id: 'TRANSCRIBING', label: 'Transcript' },
    { id: 'CHOOSE_MODE', label: 'Mode' },
    { id: 'BUILD_CLIPS', label: 'Build Clips' },
    { id: 'EXPORT',      label: 'Export' },
];

const STATE_TO_STEP = {
    HOME:         -1,
    UPLOAD:        0,
    TRANSCRIBING:  1,
    READY:         1,
    CHOOSE_MODE:   2,
    BUILD_CLIPS:   3,
    EXPORT:        4,
};

export function renderWizard(currentState, onHomeClick) {
    const root = document.getElementById('wizard-root');
    if (!root) return;

    const activeIdx = STATE_TO_STEP[currentState] ?? -1;
    const isHome = currentState === 'HOME';

    // Navbar (Top)
    const navbarHTML = `
        <div class="top-navbar">
            <a class="nav-logo" href="#" id="wizard-home-btn" aria-label="Go to home">
                <div class="nav-logo-icon">🎬</div>
                <span>ShortReel</span>
            </a>
            ${!isHome ? `<div class="nav-project-badge">Project Workspace</div>` : ''}
            <a class="nav-home-link" href="#" id="wizard-home-link">← Home</a>
        </div>
    `;

    // Workflow Header (Bottom)
    let workflowHTML = '';
    if (!isHome) {
        const stepsHTML = STEPS.map((step, i) => {
            const isDone   = i < activeIdx;
            const isActive = i === activeIdx;
            
            let icon = '○';
            let statusClass = '';
            
            if (isDone) {
                icon = '✓';
                statusClass = 'completed';
            } else if (isActive) {
                icon = '●';
                statusClass = 'active';
            }

            const connector = i < STEPS.length - 1
                ? `<div class="breadcrumb-connector ${isDone ? 'filled' : ''}"></div>`
                : '';

            return `
                <div class="breadcrumb-step ${statusClass}">
                    <span class="breadcrumb-icon">${icon}</span>
                    <span class="breadcrumb-label">${step.label}</span>
                </div>
                ${connector}
            `;
        }).join('');

        workflowHTML = `
            <div class="workflow-header">
                <div class="breadcrumb-container">
                    ${stepsHTML}
                </div>
            </div>
        `;
    }

    root.innerHTML = navbarHTML + workflowHTML;

    // Attach events
    const homeBtn = document.getElementById('wizard-home-btn');
    const homeLink = document.getElementById('wizard-home-link');
    if (homeBtn) homeBtn.addEventListener('click', (e) => { e.preventDefault(); onHomeClick(); });
    if (homeLink) homeLink.addEventListener('click', (e) => { e.preventDefault(); onHomeClick(); });
}
