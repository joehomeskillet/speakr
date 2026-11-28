import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { expect, it, vi } from 'vitest';
import { SessionTransportNotice, SettingsView } from '../../../static/js/clinic/compose-settings.js';
import { RecordingView } from '../../../static/js/clinic/compose-recording.js';
import { CorrectionBody } from '../../../static/js/clinic/compose-correction.js';

const sandbox = {};
runInNewContext(readFileSync(new URL('../../../static/vendor/js/vue.global.js', import.meta.url), 'utf8'), sandbox);
const Vue = sandbox.Vue;

const node = (tag, text = '') => ({ tag, text, props: {}, children: [] });
const renderer = Vue.createRenderer({
    createElement: node,
    createComment: () => node('comment'),
    setElementText: (el, text) => { el.text = text; },
    patchProp: (el, key, _old, value) => { el.props[key] = value; },
    insert(el, parent, anchor) {
        parent.children.splice(anchor ? parent.children.indexOf(anchor) : parent.children.length, 0, el);
        el.parent = parent;
    },
    remove(el) { el.parent.children.splice(el.parent.children.indexOf(el), 1); },
    parentNode: (el) => el.parent,
    nextSibling: (el) => el.parent.children[el.parent.children.indexOf(el) + 1],
});

it.each([
    ['local', false, 'An diesem Arbeitsplatz:', false],
    ['rdp', false, 'Remotedesktop (RDP): Geräteweiterleitung nicht geprüft.', true],
    ['ica', false, 'Citrix (ICA): Geräteweiterleitung nicht geprüft.', true],
    ['rdp', 'true', 'Die Nutzung weitergeleiteter Geräte wird nicht unterstützt.', true],
    ['ica', true, 'Citrix (ICA): Geräteweiterleitung geprüft.', false],
    [undefined, undefined, 'Der Gerätestatus dieser Sitzung konnte nicht geprüft werden.', true],
])('renders transport mode %s with verification %s', async (mode, verified, text, blocked) => {
    const client = { request: vi.fn().mockResolvedValue({
        outcome: 'ok', data: { transport_mode: mode, device_redirection_verified: verified },
    }) };
    const root = node('root');
    const app = renderer.createApp(SessionTransportNotice, { client, vue: Vue });
    app.mount(root);
    await client.request.mock.results[0].value;
    await Vue.nextTick();
    const [status] = root.children;
    const [notice] = status.children;
    expect(status.props.role).toBe('status');
    expect(notice.text).toContain(text);
    expect(notice.props.class.includes('warning')).toBe(blocked);
    expect(client.request.mock.calls).toEqual([['/preferences/workstations']]);
    app.unmount();
});

it('mounts the shared status in settings', () => {
    expect(SettingsView.components.SessionTransportNotice).toBe(SessionTransportNotice);
    expect(SettingsView.template).toContain('<SessionTransportNotice ');
});

it('mounts the shared status in recording through its setup binding', () => {
    const setup = RecordingView.setup({ vue: { ref: Vue.ref, computed: Vue.computed }, currentUser: { id: 7 }, recorder: {}, uploadFile: vi.fn() }, {});
    expect(setup.SessionTransportNotice).toBe(SessionTransportNotice);
    expect(RecordingView.template).toContain('<component :is="SessionTransportNotice" ');
});

it('mounts the shared status in the correction body through its setup binding', () => {
    const source = readFileSync(new URL('../../../static/js/clinic/compose-correction.js', import.meta.url), 'utf8');
    expect([...source.matchAll(/from '\.\/compose-settings\.js'/g)]).toHaveLength(1);
    const setup = CorrectionBody.setup({ vue: Vue, currentUser: { id: 7 }, dictationId: 'd1' }, {});
    expect(setup.SessionTransportNotice).toBe(SessionTransportNotice);
    expect(CorrectionBody.template).toContain('<component :is="SessionTransportNotice" ');
});
