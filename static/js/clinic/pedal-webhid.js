// Philips Foot Control USB profiles and button bits from the published Dictation Device SDK.
// https://github.com/corticph/dictation-device-sdk/tree/main/src
const FILTERS = Object.freeze([0x1844, 0x091a].map((productId) =>
    Object.freeze({ vendorId: 0x0911, productId, usagePage: 1, usage: 4 })));
const BUTTONS = ['rewind', 'hold', 'skip']; // Left, middle, right. The fourth pedal has no action.

function findSession(devices) {
    for (const device of devices) {
        const filter = FILTERS.find(({ vendorId, productId }) =>
            device.vendorId === vendorId && device.productId === productId);
        if (!filter) continue;
        const reports = device.collections?.find(({ usagePage, usage }) =>
            usagePage === filter.usagePage && usage === filter.usage)?.inputReports;
        if (reports?.length === 1) return { device, reportId: reports[0].reportId };
    }
    return null;
}

export function createPedalWebHid(hid) {
    const available = typeof hid?.requestDevice === 'function';
    const listeners = new Set();
    let status = available ? 'idle' : 'unavailable';
    let session = null;
    let connecting = false;
    let held = 0;

    function setStatus(next) {
        status = next;
        for (const listener of listeners) listener.onStatus?.(status);
    }

    function updateButtons(next) {
        const changed = held ^ next;
        held = next;
        BUTTONS.forEach((action, index) => {
            const bit = 1 << index;
            if (changed & bit) {
                for (const listener of listeners) listener.onButton?.(action, Boolean(next & bit));
            }
        });
    }

    function onReport(event) {
        if (!event.isTrusted || !session || event.device !== session.device || event.reportId !== session.reportId) return;
        if (event.data instanceof DataView && event.data.byteLength) updateButtons(event.data.getUint8(0));
    }

    function dropSession() {
        const { device } = session;
        session = null;
        device.removeEventListener('inputreport', onReport);
        updateButtons(0);
        return device;
    }

    if (available) {
        hid.addEventListener('disconnect', (event) => {
            if (!event.isTrusted || !session || event.device !== session.device) return;
            dropSession();
            setStatus('disconnected');
        });
    }

    async function connect() {
        if (!available || connecting || session || status === 'disconnecting') return;
        connecting = true;
        setStatus('connecting');
        let selected = null;
        try {
            const devices = await hid.requestDevice({ filters: FILTERS });
            if (!devices.length) {
                setStatus('idle');
                return;
            }
            selected = findSession(devices);
            if (!selected) {
                setStatus('unsupported');
                return;
            }
            const { device } = selected;
            session = selected;
            if (!device.opened) await device.open();
            if (session !== selected) {
                if (device.opened) await device.close();
                return;
            }
            device.addEventListener('inputreport', onReport);
            setStatus('connected');
        } catch {
            if (selected && session !== selected) return;
            if (session) dropSession();
            setStatus('error');
        } finally {
            connecting = false;
        }
    }

    async function disconnect() {
        if (status !== 'connected') return;
        setStatus('disconnecting');
        const device = dropSession();
        try {
            if (device.opened) await device.close();
            setStatus('disconnected');
        } catch {
            setStatus('error');
        }
    }

    return {
        connect,
        disconnect,
        subscribe(listener) {
            listeners.add(listener);
            listener.onStatus?.(status);
            return () => listeners.delete(listener);
        },
    };
}

// Keep the connection for this tab across navigation from settings to the active dictation view.
let shared;
export function getPedalWebHid() {
    return shared ??= createPedalWebHid(globalThis.navigator?.hid);
}
