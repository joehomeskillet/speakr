const buttons = Object.freeze({
    f1: 0x0002, f2: 0x0004, f3: 0x0008, f4: 0x0010, command: 0x0020,
    record: 0x0100, stop: 0x0200, play: 0x0400, forward: 0x0800,
    rewind: 0x1000, priority: 0x2000, insertOverwrite: 0x4000, instruction: 0x8000,
});
const slider = Object.freeze({ record: 0x0100, stop: 0x0200, play: 0x0400, rewind: 0x1000 });
const led = Object.freeze({ reportId: 0, command: 0x02 });
const speechMike = Object.freeze({
    filter: Object.freeze({ vendorId: 0x0911, productId: 0x0c1c, usagePage: 0xffa0, usage: 1 }),
    inputReportId: 0,
    inputCommand: 0x80,
    modelQuery: Object.freeze({ reportId: 0, command: 0x8b }),
    models: Object.freeze({
        3700: Object.freeze({ name: 'Philips SpeechMike SMP3700', buttons, slider: null, led }),
        3710: Object.freeze({ name: 'Philips SpeechMike SMP3710', buttons, slider, led }),
    }),
});

function inputData(event, device, command) {
    const { data } = event;
    return event.device === device && event.reportId === speechMike.inputReportId &&
        data instanceof DataView && data.byteLength >= 9 && data.getUint8(0) === command ? data : null;
}

function decode(data, model) {
    const bits = data.getUint16(7, true);
    const sliderBits = Object.values(model.slider || {}).reduce((mask, value) => mask | value, 0);
    const position = bits & sliderBits;
    const slide = Object.keys(model.slider || {}).find((name) => model.slider[name] === position) || null;
    if (model.slider && !slide) return null;
    return {
        buttons: Object.keys(model.buttons).filter((name) => (bits & ~sliderBits & model.buttons[name]) !== 0),
        slide,
    };
}

export function createHandmicController(hid, { onState = () => {}, onInput = () => {} } = {}) {
    let session = null;
    let disposed = false;
    let pairing = false;
    let closing = null;

    async function release(current) {
        if (session !== current) return;
        session = null;
        current.cancelModelQuery?.();
        current.device.removeEventListener('inputreport', current.input);
        if (current.device.opened) await current.device.close();
    }

    function onDisconnect(event) {
        const current = session;
        if (!current || current.device !== event.device) return;
        closing = release(current).catch((error) => console.warn('WebHID close failed', error)).finally(() => { closing = null; });
        onInput({ buttons: [], slide: null });
        onState({ status: 'disconnected', ledAvailable: false });
    }

    function readModel(current) {
        return new Promise((resolve, reject) => {
            const { device } = current;
            const { reportId, command } = speechMike.modelQuery;
            const finish = (error, model) => {
                if (!current.cancelModelQuery) return;
                clearTimeout(timer);
                device.removeEventListener('inputreport', listener);
                current.cancelModelQuery = null;
                if (error) reject(error);
                else resolve(model);
            };
            const listener = (event) => {
                const data = inputData(event, device, command);
                if (!data) return;
                const code = Math.max(data.getUint16(2), data.getUint16(4), data.getUint16(6));
                finish(null, data.getUint8(1) === 0 ? speechMike.models[code] : undefined);
            };
            const timer = setTimeout(() => finish(new Error('WebHID model response timed out')), 3000);
            current.cancelModelQuery = () => finish(null);
            device.addEventListener('inputreport', listener);
            device.sendReport(reportId, Uint8Array.of(command)).catch((error) => finish(error));
        });
    }

    hid?.addEventListener('disconnect', onDisconnect);
    return {
        async pair() {
            if (!hid || disposed || pairing || session) return;
            pairing = true;
            let current;
            onState({ status: 'pairing', ledAvailable: false });
            try {
                const { filter } = speechMike;
                const devices = await hid.requestDevice({ filters: [filter] });
                if (disposed) return;
                if (!devices.length) {
                    onState({ status: 'disconnected', ledAvailable: false });
                    return;
                }
                let output;
                const outputId = speechMike.modelQuery.reportId;
                const device = devices.find((candidate) => candidate.vendorId === filter.vendorId &&
                    candidate.productId === filter.productId && candidate.collections.some((item) =>
                        item.usagePage === filter.usagePage && item.usage === filter.usage &&
                        item.inputReports.some((report) => report.reportId === speechMike.inputReportId) &&
                        (output = item.outputReports.find((report) => report.reportId === outputId))));
                if (!device) {
                    onState({ status: 'unsupported', ledAvailable: false });
                    return;
                }
                current = { device, input: (event) => {
                    const data = inputData(event, device, speechMike.inputCommand);
                    const input = data && decode(data, current.model);
                    if (input) onInput(input);
                } };
                if (closing) await closing;
                if (disposed) return;
                session = current;
                await device.open();
                if (session !== current) {
                    if (device.opened) await device.close();
                    return;
                }
                current.model = await readModel(current);
                if (session !== current) return;
                if (!current.model) {
                    await release(current);
                    onState({ status: 'unsupported', ledAvailable: false });
                    return;
                }
                const outputBits = output.items.reduce((sum, item) => sum + item.reportSize * item.reportCount, 0);
                current.led = outputBits >= 72 ? current.model.led : null;
                device.addEventListener('inputreport', current.input);
                onState({ status: 'connected', name: current.model.name, ledAvailable: Boolean(current.led) });
            } catch (error) {
                const failed = !disposed && (!current || session === current);
                if (current) await release(current).catch((closeError) => console.warn('WebHID close failed', closeError));
                if (failed && !disposed) {
                    console.warn('WebHID pairing failed', error);
                    onState({ status: 'error', ledAvailable: false });
                }
            } finally {
                pairing = false;
            }
        },
        async syncLed(state) {
            const current = session;
            if (!current?.led || !current.device.opened) return 'unavailable';
            const mode = { off: 0, standby: 4, recording: 12 };
            if (!Object.hasOwn(mode, state)) throw new RangeError('Unknown handmic LED state');
            const report = Uint8Array.of(current.led.command, 0, 0, 0, 0, 0, mode[state], 0, 0);
            try {
                await current.device.sendReport(current.led.reportId, report);
                return session === current ? 'sent' : 'unavailable';
            } catch (error) {
                if (session !== current) return 'unavailable';
                current.led = null;
                console.warn('WebHID LED output failed', error);
                onState({ status: 'connected', name: current.model.name, ledAvailable: false });
                return 'unavailable';
            }
        },
        async dispose() {
            disposed = true;
            hid?.removeEventListener('disconnect', onDisconnect);
            if (session) {
                const released = release(session);
                onInput({ buttons: [], slide: null });
                await released;
            }
            if (closing) await closing;
        },
    };
}
