import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import { CorrectionView, resolveClinicOpenTarget } from '../../../static/js/clinic/compose-correction.js';

const author = { id: 7, role: 'author' };
const typist = { id: 9, role: 'typist' };
const worklist = (user) => ({ view: 'clinic-worklist', user });

const open = (payload, ctx) => resolveClinicOpenTarget(payload, ctx);

describe('PAR-WF-001 open dispatch', () => {
    it('routes an own author case from the worklist to its details, for every case kind', () => {
        for (const source_kind of ['audio', 'recognized', 'document']) {
            expect(open({
                dictationId: 'd-' + source_kind, mode: 'author_case', source_kind, status: 'zu_schreiben', author_user_id: 7,
            }, worklist(author))).toEqual({ surface: 'details', dictationId: 'd-' + source_kind, openMode: null });
        }
    });

    it('keeps the Aufnahmen row control on the editor so an author still reaches the mounted correction view', () => {
        expect(open({ dictationId: 'own', mode: 'author_case', author_user_id: 7 }, { view: 'clinic-recordings', user: author }))
            .toEqual({ surface: 'correction', dictationId: 'own', openMode: 'correction' });
    });

    it('opens own case details for an author who also works as a typist', () => {
        expect(open({ dictationId: 'own', mode: 'author_case', author_user_id: 9 }, worklist(typist)))
            .toEqual({ surface: 'details', dictationId: 'own', openMode: null });
    });

    it('sends a permitted typist to the player, the correction editor, or stays closed', () => {
        expect(open({
            dictationId: 'audio', mode: 'player', status: 'zu_schreiben', source_kind: 'audio', author_user_id: 7,
        }, worklist(typist))).toEqual({ surface: 'correction', dictationId: 'audio', openMode: 'player' });
        expect(open({
            dictationId: 'recognized', mode: 'correction', status: 'zu_korrigieren', source_kind: 'recognized', author_user_id: 7,
        }, worklist(typist))).toEqual({ surface: 'correction', dictationId: 'recognized', openMode: 'correction' });
        expect(open({
            dictationId: 'document', mode: 'player', status: 'zu_schreiben', source_kind: 'document', author_user_id: 7,
        }, worklist(typist))).toEqual({ surface: 'correction', dictationId: 'document', openMode: 'correction' });
        expect(open({ dictationId: 'mode-only', mode: 'player' }, worklist(typist)).openMode).toBe('player');
        expect(open({ dictationId: 'mode-only', mode: 'correction' }, worklist(typist)).openMode).toBe('correction');
    });

    it('leaves a denied row closed', () => {
        expect(open({ dictationId: 'x', mode: 'player', permitted: false }, worklist(typist))).toBeNull();
        expect(open({ dictationId: 'x', mode: 'correction', denied: true }, worklist(typist))).toBeNull();
        expect(open({ dictationId: 'x', mode: 'author_case', author_user_id: 8 }, worklist(author))).toBeNull();
        expect(open({ dictationId: 'x', mode: 'author_case', author_user_id: 7 }, worklist(typist))).toBeNull();
        expect(open({ dictationId: '', mode: 'player' }, worklist(typist))).toBeNull();
        expect(open({ dictationId: 'x', mode: 'custom_mode' }, worklist(typist))).toBeNull();
        expect(open(null, worklist(typist))).toBeNull();
    });

    it('marks the player and the editor on the correction view and wires the shell', () => {
        const body = CorrectionView.components.CorrectionBody.template;
        expect(body).toContain('data-testid="clinic-correction-view"');
        expect(body).toContain(':data-open-mode="openMode === \'player\' ? \'player\' : \'correction\'"');
        expect(body).toContain('TEXT.playerPage');
        expect(body).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
        expect(CorrectionView.template).toContain(':open-mode="openMode"');
        const app = readFileSync('static/js/app.modular.js', 'utf8');
        const html = readFileSync('templates/index.html', 'utf8');
        expect(app).toContain('resolveClinicOpenTarget');
        expect(app).toContain("target.surface === 'details'");
        expect(app).toContain("currentView.value = 'clinic-detail'");
        expect(html).toContain('@open="openClinicDictation"');
        expect(app).toContain("typeof source === 'string' ? source : currentView.value");
        expect(html).toContain(':open-mode="clinicOpenMode"');
    });
});
