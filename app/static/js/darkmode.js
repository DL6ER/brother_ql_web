// --- Dark mode logic ---
const prefersDark = window.matchMedia('(prefers-color-scheme: dark)');
function setDarkMode(mode) {
if (mode === 'dark') {
        document.body.classList.add('dark-mode');
        document.getElementById('darkModeIcon').innerHTML = '<span class="fas fa-moon"></span>';
        document.getElementById('darkModeIcon').style.color = '#f8d90f';
} else {
        document.body.classList.remove('dark-mode');
        document.getElementById('darkModeIcon').innerHTML = '<span class="fas fa-sun"></span>';
        document.getElementById('darkModeIcon').style.color = '#f8d90f';
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