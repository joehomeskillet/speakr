import { describe, expect, it, vi } from 'vitest';
import { LABELS, ResourcesView, RESOURCES_TEMPLATE } from '../../../static/js/clinic/compose-resources.js';

function mountResources(request) {
    const mounted = [];
    const vue = {
        reactive: (state) => state,
        computed: (get) => ({ get value() { return get(); } }),
        markRaw: (value) => value,
        ref: (value) => ({ value }),
        shallowRef: (value) => ({ value }),
        watch: () => {},
        onBeforeUnmount: () => {},
        onMounted: (callback) => mounted.push(callback),
    };
    const client = { request, switchContext: vi.fn() };
    const resources = ResourcesView.setup({ client, currentUser: { id: 7 }, vue });
    async function open(tab) {
        resources.select(tab);
        const component = resources.activeComponent.value;
        const panel = component.setup(resources.activeProps.value);
        await mounted.pop()();
        return { panel, template: component.template };
    }
    return { resources, open };
}

describe('Resources presentation', () => {
    it('binds four separate, labelled tabs to their panels and moves focus with arrow keys', () => {
        const { resources } = mountResources(vi.fn());
        expect(RESOURCES_TEMPLATE).toContain('class="tabs" role="tablist" aria-label="Ressourcen"');
        expect(RESOURCES_TEMPLATE).toMatch(/<button v-for="t in tabs"[^>]*role="tab"[^>]*class="tab justify-center"/);
        expect(RESOURCES_TEMPLATE).toContain('{{ t.label }}</button>');
        expect(RESOURCES_TEMPLATE).toContain(':aria-controls="\'res-panel-\' + t.id"');
        expect(RESOURCES_TEMPLATE).toContain('role="tabpanel" :id="\'res-panel-\' + t.id"');
        expect(resources.tabs.map(({ label }) => label)).toEqual(['Vokabular', 'Vokabelprüfung', 'Sprachbefehle', 'ASR-Profile']);
        const focus = vi.fn();
        const event = { key: 'ArrowRight', preventDefault: vi.fn(), currentTarget: { querySelector: vi.fn(() => ({ focus })) } };
        resources.onKey(event);
        expect(resources.state.active).toBe('review');
        expect(event.currentTarget.querySelector).toHaveBeenCalledWith('#res-tab-review');
        expect(event.preventDefault).toHaveBeenCalledOnce();
        expect(focus).toHaveBeenCalledOnce();
    });

    it('labels vocabulary filters and row status in German while keeping the API values', async () => {
        const request = vi.fn(async () => ({
            outcome: 'ok', data: { entries: [{ id: 'one', written: 'Beispielwort', status: 'active', version: 1 }] },
        }));
        const { panel, template } = await mountResources(request).open('vocabulary');
        expect(panel.modes.map(panel.enumLabel)).toEqual(['Enthält', 'Beginnt mit', 'Endet mit', 'Genau gleich']);
        expect(panel.filters.map(panel.enumLabel)).toEqual(['Aktiv', 'Unterdrückt', 'Alle']);
        expect(panel.enumLabel(panel.state.entries[0].status)).toBe('Aktiv');
        expect(template).toContain(':value="mode">{{ enumLabel(mode) }}');
        expect(template).toContain(':value="filter">{{ enumLabel(filter) }}');
        expect(template).toContain('{{ enumLabel(e.status) }}');
        expect(request).toHaveBeenCalledWith('/vocabulary/search?q=&mode=contains&status=active&scope=user');
    });

    it.each(['vocabulary', 'review', 'commands', 'asr'])('uses friendly German text for capability errors in %s', async (tab) => {
        const response = { outcome: 'UNKNOWN', code: 'CAPABILITY_UNAVAILABLE', message: 'Das Ergebnis ist unbekannt.' };
        const { panel, template } = await mountResources(vi.fn(async () => response)).open(tab);
        const displayed = panel.view ? panel.errorMessage(panel.view.value.error) : (panel.state || panel.c.state).message;
        expect(displayed).toBe('Diese Funktion ist derzeit nicht verfügbar.');
        expect(displayed).toBe(LABELS.unavailable);
        expect(template).toMatch(panel.view ? /errorMessage\(view.error\)/ : /state.message/);
        expect(response.code).toBe('CAPABILITY_UNAVAILABLE');
    });
});
