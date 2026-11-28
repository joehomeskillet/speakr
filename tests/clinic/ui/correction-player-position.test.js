import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { fitPlayer } from '../../../static/js/clinic/correction-player-position.js';

const dock = { left: 100, top: 260 };
const editor = { left: 99, top: 160, right: 1001, bottom: 1000 };
const player = { width: 640, height: 54 };

function visible(width, height, top = 60) {
    return { left: 0, top, right: width, bottom: height };
}

describe('correction player placement', () => {
    it('wires the player placement composable exactly once', () => {
        const source = readFileSync(new URL('../../../static/js/clinic/compose-correction.js', import.meta.url), 'utf8');
        expect(source.match(/from '\.\/correction-player-position\.js'/g)).toHaveLength(1);
        expect(source).toContain('...useCorrectionPlayerPosition(V)');
    });

    it('preserves free movement inside the visible workspace', () => {
        expect(fitPlayer({ x: 160, y: 240 }, dock, player, editor, visible(1440, 900)))
            .toEqual({ x: 160, y: 240 });
    });

    it('clamps against both workspace and scrollport edges', () => {
        const area = visible(1440, 720, 200);
        expect(fitPlayer({ x: -400, y: -400 }, dock, player, editor, area)).toEqual({ x: -1, y: -60 });
        expect(fitPlayer({ x: 900, y: 900 }, dock, player, editor, area)).toEqual({ x: 261, y: 406 });
    });

    it('keeps the reflowed bar inside a 390 px viewport', () => {
        const mobileDock = { left: 14, top: 330 };
        const mobileEditor = { left: 13, top: 180, right: 377, bottom: 1300 };
        const mobilePlayer = { width: 362, height: 125 };
        expect(fitPlayer({ x: 261, y: 650 }, mobileDock, mobilePlayer, mobileEditor, visible(390, 844)))
            .toEqual({ x: 1, y: 389 });
    });

    it('stays inside the editor when its visible slice is too short to fit', () => {
        expect(fitPlayer({ x: 0, y: 900 }, dock, player, editor, visible(1440, 195, 170)))
            .toEqual({ x: 0, y: 686 });
    });
});
