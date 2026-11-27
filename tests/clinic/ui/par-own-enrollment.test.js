import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { createVoiceConsentController, encodeWav } from '../../../static/js/clinic/voice-consent-ui.js';

const ok = (data) => ({ outcome: 'ok', status: 200, data });
const NONE = { state: 'none', latest_consent_id: 0, embedding_count: 0, history: [] };
const GRANTED = { state: 'granted', latest_consent_id: 1, embedding_count: 0, history: [] };

function server() {
    let cur = NONE;
    const calls = [];
    const client = {
        switchContext: vi.fn(),
        request: vi.fn(async (path, opts = {}) => {
            calls.push({ path, ...opts });
            if (path === '/voice-consent') return ok(cur);
            if (path === '/voice-consent/grant') { cur = GRANTED; return ok(cur); }
            if (path === '/voice/enrollment') {
                cur = { ...cur, embedding_count: cur.embedding_count + 1 };
                return { outcome: 'ok', status: 201, data: { state: 'granted', consent_revision: 1, embedding_count: cur.embedding_count } };
            }
            if (path === '/voice-consent/revoke') {
                cur = { state: 'revoked', latest_consent_id: 2, embedding_count: 0, history: [] };
                return ok(cur);
            }
            return ok({});
        }),
    };
    return { client, calls };
}

describe('own voice enrollment', () => {
    it('offers no sample action without consent', async () => {
        const { client, calls } = server();
        const c = createVoiceConsentController({ client, newKey: () => 'key-00000001' });
        await c.load();
        expect(c.view().canEnroll).toBe(false);
        expect(await c.enroll(encodeWav(new Float32Array(48000)))).toBe(false);
        expect(calls.some((x) => x.path === '/voice/enrollment')).toBe(false);
    });

    it('raises the profile count after an accepted sample and returns to 0 after revoke', async () => {
        const { client, calls } = server();
        const c = createVoiceConsentController({ client, newKey: () => 'key-00000001' });
        await c.load();
        await c.grant();
        expect(c.view().canEnroll).toBe(true);
        expect(await c.enroll(encodeWav(new Float32Array(48000)))).toBe(true);
        expect(c.view().embeddingCount).toBe(1);
        const post = calls.find((x) => x.path === '/voice/enrollment');
        expect(post.method).toBe('POST');
        expect(post.ifMatch).toBe('"voice-consent:1"');
        expect(post.idempotencyKey).toBe('key-00000001');
        expect(post.formData.type).toBe('audio/wav');
        c.requestRevoke();
        await c.confirmRevoke();
        expect(c.view().embeddingCount).toBe(0);
        expect(c.view().canEnroll).toBe(false);
    });

    it('encodes 16 kHz mono 16-bit PCM WAV and rejects an empty sample', async () => {
        const wav = encodeWav(new Float32Array(16000));
        const bytes = new DataView(await wav.arrayBuffer());
        expect(bytes.getUint32(24, true)).toBe(16000);
        expect(bytes.getUint16(22, true)).toBe(1);
        expect(bytes.getUint16(34, true)).toBe(16);
        const { client } = server();
        const c = createVoiceConsentController({ client });
        await c.load();
        await c.grant();
        expect(await c.enroll(new Blob([]))).toBe(false);
        expect(c.view().error.code).toBe('INVALID_AUDIO');
    });

    it('wires the sample controls into the settings consent template', () => {
        const src = readFileSync(new URL('../../../static/js/clinic/compose-settings.js', import.meta.url), 'utf8');
        expect(src).toContain('clinic-settings-consent-record');
        expect(src).toContain('clinic-settings-consent-upload');
        expect(src).toContain('v-if="view.canEnroll');
    });
});
