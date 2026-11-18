import { defineConfig } from 'vitest/config';

export default defineConfig({
    test: {
        include: ['static/js/**/*.test.js', 'tests/clinic/ui/**/*.test.js'],
        environment: 'node',
        globals: false,
    },
});
