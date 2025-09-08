// --- Dark mode logic ---
const prefersDark = window.matchMedia('(prefers-color-scheme: dark)');

function setDarkMode(mode) {
    const navbar = document.getElementById('mainNavbar');
    if (mode === 'dark') {
        document.body.classList.add('dark-mode');
        document.getElementById('darkModeIcon').innerHTML = '<span class="fas fa-moon"></span>';
        document.getElementById('darkModeIcon').style.color = '#f8d90f';
        navbar.classList.remove('navbar-light', 'bg-light');
        navbar.classList.add('navbar-dark', 'bg-dark');
        // Replace all text-muted with text-light
        document.querySelectorAll('.text-muted').forEach(el => {
            el.classList.remove('text-muted');
            el.classList.add('text-light');
        });
    } else {
        document.body.classList.remove('dark-mode');
        document.getElementById('darkModeIcon').innerHTML = '<span class="fas fa-sun"></span>';
        document.getElementById('darkModeIcon').style.color = '#f8d90f';
        navbar.classList.remove('navbar-dark', 'bg-dark');
        navbar.classList.add('navbar-light', 'bg-light');
        // Replace all text-light with text-muted
        document.querySelectorAll('.text-light').forEach(el => {
            el.classList.remove('text-light');
            el.classList.add('text-muted');
        });
    }
}

function getStoredMode() {
    return localStorage.getItem('themeMode');
}
function storeMode(mode) {
    localStorage.setItem('themeMode', mode);
}
function getPreferredMode() {
    const stored = getStoredMode();
    if (stored) return stored;
    return prefersDark.matches ? 'dark' : 'light';
}

function toggleDarkMode() {
    const current = document.body.classList.contains('dark-mode') ? 'dark' : 'light';
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
        toggle.title = 'Toggle dark mode';
    }
});