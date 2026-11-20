import { describe, expect, it, vi } from 'vitest';
import { asrCapabilityLabel, createAsrProfilesController } from '../../../static/js/clinic/asr-profiles-ui.js';
import { createVoiceConsentController } from '../../../static/js/clinic/voice-consent-ui.js';

const ok = (data) => ({ outcome: 'ok', data });
const denied = { outcome: 'permission', message: 'Kein Zugriff' };

describe('G7 ASR and consent states', () => {
    it('names every server capability in German and does not expose unknown enums', () => {
        const names = ['batch', 'live', 'hotword', 'negative_lexicon', 'pronunciation',
            'training', 'adaptation', 'checkpoint', 'alignment', 'local_tts'];
        for (const name of names) {
            expect(asrCapabilityLabel(name)).not.toBe(name);
            expect(asrCapabilityLabel(name)).not.toContain('Unbekannt');
        }
        expect(asrCapabilityLabel('future_internal_enum')).toBe('Unbekannte Fähigkeit');
    });

    it('does not save ASR defaults before a successful read or after a denied reload', async () => {
        const client = { request: vi.fn(async () => denied) };
        const c = createAsrProfilesController({ client });
        c.setSource('recorder');
        expect(c.view().canSave).toBe(false);
        expect(await c.save()).toBe(false);
        expect(client.request).not.toHaveBeenCalled();
        await c.load();
        expect(await c.save()).toBe(false);
        expect(client.request).toHaveBeenCalledTimes(1);
    });

    it('does not claim absent consent or allow consent writes after a failed read', async () => {
        const client = { request: vi.fn(async () => denied) };
        const c = createVoiceConsentController({ client });
        expect(c.view().status.label).toBe('Einwilligungsstand nicht geladen');
        expect(await c.grant()).toBe(false);
        await c.load();
        expect(c.view().canGrant).toBe(false);
        expect(await c.grant()).toBe(false);
        expect(client.request).toHaveBeenCalledTimes(1);
    });

    it('keeps the read failure after a conflict instead of claiming a successful reload', async () => {
        const client = { request: vi.fn()
            .mockResolvedValueOnce(ok({ state: 'none', latest_consent_id: 0 }))
            .mockResolvedValueOnce({ outcome: 'conflict' })
            .mockResolvedValueOnce(denied) };
        const c = createVoiceConsentController({ client });
        await c.load();
        expect(await c.grant()).toBe(false);
        expect(c.view().error.message).toBe('Kein Zugriff');
        expect(c.view().notice).toBeNull();
        expect(c.view().canGrant).toBe(false);
    });
});
