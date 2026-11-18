// Tailwind CSS configuration for the stylesheet compiled at build time
// (scripts/build_css.sh locally, the css-stage of the Dockerfile for images).
// The output, static/css/tailwind.css, is linked from every page template.
//
// Only complete class names that appear literally in the files below are
// compiled. A class name assembled at runtime from parts (for example
// `bg-${color}-500`) is absent from the output; each full class name has to
// be written out instead.

/** @type {import('tailwindcss').Config} */
export default {
    content: [
        './templates/**/*.html',
        './static/js/**/*.js',
        './src/**/*.py',
    ],
    // The theme is applied with the "dark" class on <html> (applyTheme in the
    // templates, global-header.js), not with the system preference.
    darkMode: 'class',
    theme: {
        extend: {
            maxHeight: {
                '85vh': '85vh',
                '90vh': '90vh',
            },
            colors: {
                primary: 'var(--bg-primary)',
                secondary: 'var(--bg-secondary)',
                accent: 'var(--bg-accent)',
                'brand-primary': 'var(--brand-primary)',
                'brand-accent': 'var(--brand-accent)',
                'brand-canvas': 'var(--brand-canvas)',
                'brand-accent-darkmode': 'var(--brand-accent-darkmode)',
                'ui-surface': 'var(--ui-surface)',
                'ui-text': 'var(--ui-text)',
                'ui-text-muted': 'var(--ui-text-muted)',
                'ui-border': 'var(--ui-border)',
                'ui-hover': 'var(--ui-hover)',
            },
            fontFamily: {
                sans: ['var(--ui-font-sans)', 'Inter', 'Segoe UI', 'system-ui', 'sans-serif'],
                mono: ['var(--ui-font-mono)', 'JetBrains Mono', 'ui-monospace', 'monospace'],
            },
            borderRadius: {
                'ui-sm': 'var(--ui-radius-sm)',
                'ui-md': 'var(--ui-radius-md)',
            },
            spacing: {
                'ui-1': 'var(--ui-space-1)',
                'ui-2': 'var(--ui-space-2)',
                'ui-3': 'var(--ui-space-3)',
                'ui-4': 'var(--ui-space-4)',
                'ui-6': 'var(--ui-space-6)',
                'ui-8': 'var(--ui-space-8)',
            },
        },
    },
    plugins: [],
};
