import { afterEach, describe, expect, it, vi } from 'vitest';
import { useRecordings } from '../../../static/js/modules/composables/recordings.js';

const LIST = [{ id: 1, title: 'A' }, { id: 2, title: 'B' }];

// Every state key is a { value } ref created on first access.
function makeState() {
    const refs = {};
    const state = new Proxy({}, {
        get(_, key) {
            if (!(key in refs)) refs[key] = { value: undefined };
            return refs[key];
        },
    });
    refs.recordings = { value: [] };
    refs.perPage = { value: 25 };
    refs.windowWidth = { value: 1280 };
    refs.sortBy = { value: '' };
    return { state, refs };
}

function stubBrowser({ clinic, lastId }) {
    const store = { lastSelectedRecordingId: lastId };
    vi.stubGlobal('localStorage', {
        getItem: (k) => (k in store ? store[k] : null),
        setItem: (k, v) => { store[k] = String(v); },
    });
    vi.stubGlobal('document', {
        getElementById: (id) => (id === 'app' ? { dataset: { clinicMode: clinic } } : null),
    });
    vi.stubGlobal('fetch', vi.fn(async (url) => {
        if (String(url).startsWith('/api/recordings?')) {
            return { ok: true, json: async () => ({ recordings: LIST, pagination: null }) };
        }
        const id = Number(String(url).split('/').pop());
        return { ok: true, json: async () => ({ id, title: 'full', status: 'COMPLETED' }) };
    }));
}

async function load() {
    const { state, refs } = makeState();
    const { loadRecordings } = useRecordings(state, { setGlobalError: vi.fn(), showToast: vi.fn() }, null);
    await loadRecordings(1, false, '');
    return refs;
}

afterEach(() => vi.unstubAllGlobals());

describe('restore of the last opened recording on load', () => {
    it('clinic mode on: opens no recording', async () => {
        stubBrowser({ clinic: 'True', lastId: '2' });
        const refs = await load();
        expect(refs.selectedRecording.value).toBeUndefined();
        expect(fetch.mock.calls.some(([u]) => u === '/api/recordings/2')).toBe(false);
    });

    it('clinic mode on: opening one from the list still works', async () => {
        stubBrowser({ clinic: 'True', lastId: '2' });
        const { state, refs } = makeState();
        const { selectRecording } = useRecordings(state, { setGlobalError: vi.fn(), showToast: vi.fn() }, null);
        await selectRecording(LIST[0]);
        expect(refs.selectedRecording.value.id).toBe(1);
        expect(refs.currentView.value).toBe('detail');
    });

    it('clinic mode off: upstream behaviour, last recording is restored', async () => {
        stubBrowser({ clinic: 'False', lastId: '2' });
        const refs = await load();
        expect(refs.selectedRecording.value.id).toBe(2);
    });
});
