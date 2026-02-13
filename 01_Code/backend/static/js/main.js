document.addEventListener('DOMContentLoaded', () => {
    if (window.lucide) {
        window.lucide.createIcons();
    }

    const root = document.documentElement;
    const themeToggle = document.getElementById('themeToggle');
    const savedTheme = localStorage.getItem('clarifai-theme');

    if (savedTheme === 'light') {
        root.classList.add('light');
    }

    if (themeToggle) {
        themeToggle.addEventListener('click', () => {
            root.classList.toggle('light');
            localStorage.setItem('clarifai-theme', root.classList.contains('light') ? 'light' : 'dark');
        });
    }

    document.querySelectorAll('[data-open-modal]').forEach((button) => {
        button.addEventListener('click', () => {
            const target = document.getElementById(button.dataset.openModal);
            if (target) {
                target.classList.add('active');
            }
        });
    });

    document.querySelectorAll('[data-close-modal]').forEach((button) => {
        button.addEventListener('click', () => {
            const target = button.closest('.modal');
            if (target) {
                target.classList.remove('active');
            }
        });
    });

    document.querySelectorAll('[data-toggle-password]').forEach((button) => {
        button.addEventListener('click', () => {
            const fieldId = button.getAttribute('data-toggle-password');
            const input = document.getElementById(fieldId);
            if (!input) {
                return;
            }

            const show = input.type === 'password';
            input.type = show ? 'text' : 'password';
            button.textContent = show ? 'Hide' : 'Peek';
        });
    });
});
