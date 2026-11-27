import { afterEach, describe, expect, it, vi } from 'vitest';
import { createPedalSettingsComponent } from '../../../static/js/clinic/compose-settings.js';
import { createPedalController } from '../../../static/js/clinic/pedal-ui.js';
import { createPedalWebHid } from '../../../static/js/clinic/pedal-webhid.js';

function hidEvents() {
    const listeners = new Map();
    return {
        addEventListener: (type, listener) => listeners.set(type, listener),
        removeEventListener: (type) => listeners.delete(type),
        emit(type, event) { listeners.get(type)?.({ isTrusted: true, ...event }); },
    };
}

function footControl(productId = 0x091a) {
    const device = {
        ...hidEvents(), vendorId: 0x0911, productId, opened: false,
        collections: [{ usagePage: 1, usage: 4, inputReports: [{ reportId: 0 }] }],
        open: vi.fn(async () => { device.opened = true; }),
        close: vi.fn(async () => { device.opened = false; }),
        report(...bytes) {
            device.emit('inputreport', { device, reportId: 0, data: new DataView(Uint8Array.from(bytes).buffer) });
        },
    };
    return device;
}

function mockNavigator(device) {
    const hid = { ...hidEvents(), requestDevice: vi.fn(async () => [device]) };
    vi.stubGlobal('navigator', { hid });
    return hid;
}

afterEach(() => vi.unstubAllGlobals());

describe('foot pedal WebHID (synthetic fixture)', () => {
    it('requests only from a trusted settings activation and reports unsupported devices', async () => {
        const device = footControl(0xffff);
        const hid = mockNavigator(device);
        const webHid = createPedalWebHid(navigator.hid);
        const vue = { ref: (value) => ({ value }), onBeforeUnmount: vi.fn() };
        const settings = createPedalSettingsComponent(vue, { webHid }).setup();

        expect(settings.webHidStatus.value).toBe('idle');
        await settings.toggleWebHid({ isTrusted: false });
        expect(hid.requestDevice).not.toHaveBeenCalled();
        await settings.toggleWebHid({ isTrusted: true });
        expect(hid.requestDevice).toHaveBeenCalledTimes(1);
        expect(hid.requestDevice).toHaveBeenCalledWith({ filters: [
            { vendorId: 0x0911, productId: 0x1844, usagePage: 1, usage: 4 },
            { vendorId: 0x0911, productId: 0x091a, usagePage: 1, usage: 4 },
        ] });
        expect(device.open).not.toHaveBeenCalled();
        expect(settings.webHidStatus.value).toBe('unsupported');

        const unavailable = createPedalSettingsComponent(vue, { webHid: createPedalWebHid(undefined) }).setup();
        expect(unavailable.webHidStatus.value).toBe('unavailable');
    });

    it('uses report edges for rewind, hold and skip, and releases the hold on disconnect synchronously', async () => {
        const device = footControl(0x1844);
        const hid = mockNavigator(device);
        const webHid = createPedalWebHid(navigator.hid);
        const states = [];
        webHid.subscribe({ onStatus: (status) => states.push(status) });
        const commands = { rewind: vi.fn(), play: vi.fn(), pause: vi.fn(), skip: vi.fn() };
        const pedal = createPedalController({ commands, webHid, settings: { skipSeconds: 5 } });
        await webHid.connect(); // The settings component can unmount before a playback view attaches.
        pedal.attach();
        device.report(0b0111, 0, 0);
        device.report(0b0111, 0, 0);
        expect(commands.rewind.mock.calls).toEqual([[3]]);
        expect(commands.play).toHaveBeenCalledTimes(1);
        expect(commands.skip.mock.calls).toEqual([[5]]);
        device.report();
        device.emit('inputreport', { device, reportId: 1, data: new DataView(new ArrayBuffer(1)) });
        device.emit('inputreport', { device, isTrusted: false, reportId: 0, data: new DataView(new ArrayBuffer(1)) });
        hid.emit('disconnect', { device: footControl() });
        expect(commands.pause).not.toHaveBeenCalled();
        expect(states.at(-1)).toBe('connected');

        hid.emit('disconnect', { device });
        expect(commands.pause).toHaveBeenCalledTimes(1);
        expect(states.at(-1)).toBe('disconnected');
        device.report(0b0010);
        expect(commands.play).toHaveBeenCalledTimes(1);
        pedal.detach();
        expect(commands.pause).toHaveBeenCalledTimes(1);
        expect(states).toEqual(['idle', 'connecting', 'connected', 'disconnected']);
    });

    it('releases recording on pedal up and on explicit disconnect', async () => {
        const device = footControl();
        const webHid = createPedalWebHid(mockNavigator(device));
        const release = vi.fn();
        const hold = vi.fn(() => release);
        const pedal = createPedalController({ context: 'recording', commands: { hold }, webHid });
        pedal.attach();
        await webHid.connect();
        device.report(0b1000);
        expect(hold).not.toHaveBeenCalled();
        device.report(0b0010);
        device.report(0);
        expect(release).toHaveBeenCalledTimes(1);
        device.report(0b0010);
        const disconnect = webHid.disconnect();
        expect(release).toHaveBeenCalledTimes(2);
        await disconnect;
        expect(device.close).toHaveBeenCalledTimes(1);
        pedal.detach();
    });
});
