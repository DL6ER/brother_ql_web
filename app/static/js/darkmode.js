// --- Dark mode logic ---
// Uses the colour modes of Bootstrap (data-bs-theme on <html>). The initial
// mode is already set by a script in the page head.
const prefersDark = window.matchMedia('(prefers-color-scheme: dark)');

function setDarkMode(mode) {
    document.documentElement.setAttribute('data-bs-theme', mode);
    const icon = document.getElementById('darkModeIcon');
    if (icon) {
        icon.className = mode === 'dark' ? 'fas fa-moon' : 'fas fa-sun';
    }
}

function getStoredMode() {
    try {
        return localStorage.getItem('themeMode');
    } catch (e) {
        return null;
    }
}
function storeMode(mode) {
    try {
        localStorage.setItem('themeMode', mode);
    } catch (e) { }
}
function getPreferredMode() {
    const stored = getStoredMode();
    if (stored === 'dark' || stored === 'light') return stored;
    return prefersDark.matches ? 'dark' : 'light';
}

function toggleDarkMode() {
    const current = document.documentElement.getAttribute('data-bs-theme') === 'dark' ? 'dark' : 'light';
    const next = current === 'dark' ? 'light' : 'dark';
    setDarkMode(next);
    storeMode(next);
}

document.addEventListener('DOMContentLoaded', function () {
    // Set initial mode
    setDarkMode(getPreferredMode());
    // Listen for system changes
    prefersDark.addEventListener('change', function (e) {
        if (!getStoredMode()) setDarkMode(e.matches ? 'dark' : 'light');
    });

    // Toggle button
    const toggle = document.getElementById('darkModeToggle');
    if (toggle) {
        toggle.onclick = toggleDarkMode;
    }
});
