import { describe, expect, it, vi } from 'vitest';
import { createHandmicController } from '../../../static/js/clinic/handmic-webhid.js';

function fixture({ model = 3710, productId = 0x0c1c, usagePage = 0xffa0, outputSize = 9 } = {}) {
    const device = Object.assign(new EventTarget(), {
        vendorId: 0x0911, productId, opened: false,
        collections: [{ usagePage, usage: 1, inputReports: [{ reportId: 0 }],
            outputReports: [{ reportId: 0, items: [{ reportSize: 8, reportCount: outputSize }] }] }],
    });
    function report(command, word = 0, { reportId = 0, length = 9 } = {}) {
        const data = new DataView(new ArrayBuffer(length + 2), 2);
        data.setUint8(0, command);
        if (length >= 9) {
            if (command === 0x8b) data.setUint16(4, model);
            else data.setUint16(7, word, true);
        }
        device.dispatchEvent(Object.assign(new Event('inputreport'), { device, reportId, data }));
    }
    device.open = vi.fn(async () => { device.opened = true; });
    device.close = vi.fn(async () => { device.opened = false; });
    device.sendReport = vi.fn(async (_id, bytes) => { if (bytes[0] === 0x8b) report(0x8b); });
    const hid = Object.assign(new EventTarget(), { requestDevice: vi.fn(async () => [device]) });
    const onInput = vi.fn();
    const onState = vi.fn();
    const controller = createHandmicController(hid, { onInput, onState });
    return { controller, device, hid, onInput, onState, report };
}

describe('handmic WebHID (synthetic reports)', () => {
    it.each([[3710, ['f1', 'forward'], 'play'], [3700, ['f1', 'play', 'forward'], null]])(
        'decodes button snapshots and model-specific slide positions for %i', async (model, buttons, slide) => {
            const { controller, hid, onInput, report } = fixture({ model });
            await controller.pair();
            expect(hid.requestDevice).toHaveBeenCalledWith({ filters: [
                { vendorId: 0x0911, productId: 0x0c1c, usagePage: 0xffa0, usage: 1 },
            ] });
            report(0x80, 0x0c02);
            expect(onInput).toHaveBeenLastCalledWith({ buttons, slide });
            report(0x80, 0x0200);
            expect(onInput).toHaveBeenLastCalledWith(model === 3710 ? { buttons: [], slide: 'stop' } : { buttons: ['stop'], slide: null });
            report(0x91);
            report(0x80, 0, { length: 8 });
            report(0x80, 0x0100, { reportId: 1 });
            if (model === 3710) report(0x80, 0x0600);
            expect(onInput).toHaveBeenCalledTimes(2);
            await controller.dispose();
        },
    );

    it.each([{ productId: 0x1f40 }, { vendorId: 1 }, { usagePage: 1 }, { model: 3720 }])(
        'fails closed for an undeclared contract: %o', async (options) => {
            const { controller, device, onInput, onState, report } = fixture(options);
            if (options.vendorId) device.vendorId = options.vendorId;
            await controller.pair();
            expect(onState).toHaveBeenLastCalledWith({ status: 'unsupported', ledAvailable: false });
            if (!options.model) expect(device.open).not.toHaveBeenCalled();
            expect(device.opened).toBe(false);
            report(0x80, 0x0100);
            expect(onInput).not.toHaveBeenCalled();
            expect(await controller.syncLed('recording')).toBe('unavailable');
            expect(device.sendReport.mock.calls.map(([, bytes]) => bytes[0])).toEqual(options.model ? [0x8b] : []);
        },
    );

    it('synchronizes recording, standby and off through actual output calls', async () => {
        const { controller, device } = fixture();
        await controller.pair();
        for (const [state, red] of [['recording', 12], ['standby', 4], ['off', 0]]) {
            expect(await controller.syncLed(state)).toBe('sent');
            expect(device.sendReport).toHaveBeenLastCalledWith(0, Uint8Array.of(2, 0, 0, 0, 0, 0, red, 0, 0));
        }
        await controller.dispose();
    });

    it('reports unavailable feedback without writing to a short output report', async () => {
        const { controller, device, onState } = fixture({ outputSize: 1 });
        await controller.pair();
        expect(onState).toHaveBeenLastCalledWith({ status: 'connected', name: 'Philips SpeechMike SMP3710', ledAvailable: false });
        expect(await controller.syncLed('recording')).toBe('unavailable');
        expect(device.sendReport).toHaveBeenCalledTimes(1);
        await controller.dispose();
    });

    it('marks rejected LED output unavailable rather than reporting success or retrying', async () => {
        const { controller, device, onState } = fixture();
        await controller.pair();
        const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
        try {
            const error = new Error('Output report rejected');
            device.sendReport.mockRejectedValueOnce(error);
            expect(await controller.syncLed('recording')).toBe('unavailable');
            expect(onState).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'connected', ledAvailable: false }));
            expect(warn).toHaveBeenCalledWith('WebHID LED output failed', error);
            expect(await controller.syncLed('off')).toBe('unavailable');
            expect(device.sendReport).toHaveBeenCalledTimes(2);
        } finally {
            warn.mockRestore();
            await controller.dispose();
        }
    });

    it('clears held input and LED availability on disconnect and allows explicit re-pairing', async () => {
        const { controller, device, hid, onInput, onState, report } = fixture();
        await controller.pair();
        report(0x80, 0x0102);
        hid.dispatchEvent(Object.assign(new Event('disconnect'), { device }));
        expect(device.close).toHaveBeenCalledOnce();
        expect(onInput).toHaveBeenLastCalledWith({ buttons: [], slide: null });
        expect(onState).toHaveBeenLastCalledWith({ status: 'disconnected', ledAvailable: false });
        report(0x80, 0x0400);
        expect(onInput).toHaveBeenCalledTimes(2);
        expect(await controller.syncLed('recording')).toBe('unavailable');
        await controller.pair();
        expect(onState).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'connected', ledAvailable: true }));
        await controller.dispose();
        expect(device.close).toHaveBeenCalledTimes(2);
    });

    it('ignores a chooser result that arrives after teardown', async () => {
        const { controller, device } = fixture();
        const pairing = controller.pair();
        await controller.dispose();
        await pairing;
        expect(device.open).not.toHaveBeenCalled();
    });
});
