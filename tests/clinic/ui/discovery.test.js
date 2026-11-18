import { describe, expect, it } from 'vitest';
import config from '../../../vitest.config.js';

describe('clinic JavaScript test discovery', () => {
    it('includes both upstream and clinic tests', () => {
        expect(config.test.include).toEqual([
            'static/js/**/*.test.js',
            'tests/clinic/ui/**/*.test.js',
        ]);
    });

    it('keeps the existing Vitest runner behavior', () => {
        expect(config.test.environment).toBe('node');
        expect(config.test.globals).toBe(false);
    });
});
