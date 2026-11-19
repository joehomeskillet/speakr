/**
 * Optional dictation protection: enable, disable, unlock (time-limited grant).
 *
 * `createDictationProtectionModel` is a framework-free state holder (pass Vue's
 * `reactive` to make it reactive). `DictationProtectionComponent(Vue)` wraps it
 * as a Vue component. Requests go through the clinic api client; the server
 * (src/clinic/dictation_lock.py via the dictation routes) stays the authority
 * for lockout, grants and versions. The entered code lives only in
 * `state.secret` and is cleared after every attempt, successful or not. It is
 * never stored, logged or echoed. Protection only adds a requirement on top of
 * the access rights; it never replaces object authorization. Colours come from
 * tokens.js (no new palette).
 */

import { surfaceFor } from './tokens.js';

export const TEXT = Object.freeze({
    title: 'Diktat-Schutz',
    notice: 'Der Schutz ist eine zusätzliche Hürde. Er ersetzt keine Zugriffsberechtigung für das Diktat.',
    stateProtected: 'Dieses Diktat ist geschützt.',
    stateOpen: 'Dieses Diktat ist nicht geschützt.',
    stateUnknown: 'Der Schutzstatus wurde noch nicht geladen.',
    code: 'Schutzcode',
    confirmEnable: 'Ich bestätige, dass der Schutz für dieses Diktat gesetzt wird.',
    confirmDisable: 'Ich bestätige, dass der Schutz für dieses Diktat entfernt wird.',
    enable: 'Schutz aktivieren',
    disable: 'Schutz entfernen',
    unlock: 'Entsperren',
    retry: 'Erneut versuchen',
    reload: 'Status neu laden',
    needConfirm: 'Bitte die Bestätigung ankreuzen.',
    needCode: 'Bitte einen Schutzcode eingeben.',
    enabled: 'Der Schutz wurde aktiviert.',
    disabled: 'Der Schutz wurde entfernt.',
    unlocked: 'Entsperrt. Die Freigabe ist zeitlich begrenzt.',
    wrongCode: 'Code falsch oder keine Berechtigung.',
    locked: 'Zu viele Fehlversuche. Bitte später erneut versuchen.',
    conflict: 'Der Schutzstatus wurde zwischenzeitlich geändert. Er wurde neu geladen.',
    unknownDictation: 'Das Diktat wurde nicht gefunden.',
    permission: 'Keine Berechtigung für diese Aktion.',
    validation: 'Der Code wurde vom Server abgelehnt.',
    network: 'Die Verbindung ist fehlgeschlagen.',
    unknown: 'Das Ergebnis ist unbekannt.',
});

const EXPIRES = 'expires_at';

function path(dictationId) {
    return '/dictations/' + encodeURIComponent(dictationId) + '/protection';
}

function unlockPath(dictationId) {
    return '/dictations/' + encodeURIComponent(dictationId) + '/unlock';
}

/**
 * @param {{request: Function}} client  createClinicApiClient() instance
 * @param {string} dictationId
 * @param {{reactive?: Function}} [options]
 */
export function createDictationProtectionModel(client, dictationId, options) {
    const reactive = (options && options.reactive) || ((o) => o);
    const state = reactive({
        protected: null,
        version: null,
        secret: '',
        confirmed: false,
        status: 'idle',
        message: '',
        grantExpiresAt: null,
        lastAction: null,
    });

    function set(status, message) {
        state.status = status;
        state.message = message;
    }

    function clearInput() {
        state.secret = '';
        state.confirmed = false;
    }

    async function load() {
        state.status = 'busy';
        const res = await client.request(path(dictationId));
        if (res.outcome === 'ok' && res.data) {
            state.protected = Boolean(res.data.protected);
            state.version = res.data.version;
            if (!res.data.access) state.grantExpiresAt = null;
            set('idle', '');
        } else fail(res, 'load');
        return state;
    }

    // Maps a failed result to a state; status codes of the server decide
    // between wrong code (403), lockout (429) and stale version (409).
    function fail(res, action) {
        state.lastAction = action;
        if (res.status === 429) set('locked', TEXT.locked);
        else if (res.status === 404) set('denied', TEXT.unknownDictation);
        else if (res.outcome === 'conflict') {
            set('conflict', TEXT.conflict);
            reloadQuietly();
        } else if (res.outcome === 'permission') set('denied', action === 'unlock' ? TEXT.wrongCode : TEXT.permission);
        else if (res.outcome === 'validation') set('invalid', TEXT.validation);
        else if (res.outcome === 'network') set('retry', TEXT.network);
        else if (res.outcome === 'cancelled') set('idle', '');
        else set('retry', TEXT.unknown);
    }

    async function reloadQuietly() {
        const res = await client.request(path(dictationId));
        if (res.outcome === 'ok' && res.data) {
            state.protected = Boolean(res.data.protected);
            state.version = res.data.version;
        }
    }

    function precheck(needSecret, needConfirm) {
        if (needConfirm && !state.confirmed) { set('invalid', TEXT.needConfirm); return false; }
        if (needSecret && !state.secret.trim()) { set('invalid', TEXT.needCode); return false; }
        return true;
    }

    async function change(action, secretValue, okMessage) {
        state.status = 'busy';
        state.lastAction = action;
        const res = await client.request(path(dictationId), {
            method: 'PUT',
            body: { version: state.version, secret: secretValue },
        });
        clearInput();
        if (res.outcome === 'ok') {
            state.protected = Boolean(res.data && res.data.protected);
            state.version = res.data ? res.data.version : state.version;
            state.grantExpiresAt = null;
            set('done', okMessage);
        } else fail(res, action);
        return state;
    }

    async function enable() {
        if (!precheck(true, true)) return state;
        return change('enable', state.secret, TEXT.enabled);
    }

    async function disable() {
        if (!precheck(false, true)) return state;
        return change('disable', null, TEXT.disabled);
    }

    async function unlock() {
        if (!precheck(true, false)) return state;
        state.status = 'busy';
        state.lastAction = 'unlock';
        const res = await client.request(unlockPath(dictationId), {
            method: 'POST',
            body: { secret: state.secret },
        });
        clearInput();
        if (res.outcome === 'ok') {
            state.grantExpiresAt = (res.data && res.data[EXPIRES]) || null;
            set('done', TEXT.unlocked);
        } else fail(res, 'unlock');
        return state;
    }

    return {
        state,
        load,
        enable,
        disable,
        unlock,
        reset() { clearInput(); state.grantExpiresAt = null; set('idle', ''); },
        surface: () => surfaceFor(state.status === 'done' ? 'success'
            : state.status === 'busy' ? 'processing'
            : state.status === 'conflict' || state.status === 'locked' ? 'warning'
            : ['invalid', 'denied', 'retry'].includes(state.status) ? 'error' : 'normal'),
    };
}

/**
 * Vue component factory. Vue is passed in so the module needs no bundler.
 * Props: client, dictationId.
 */
export function DictationProtectionComponent(Vue) {
    return {
        name: 'DictationProtection',
        props: {
            client: { type: Object, required: true },
            dictationId: { type: String, required: true },
        },
        setup(props) {
            const model = createDictationProtectionModel(props.client, props.dictationId, {
                reactive: Vue.reactive,
            });
            return {
                model,
                text: TEXT,
                stateText: Vue.computed(() => (model.state.protected === null ? TEXT.stateUnknown
                    : model.state.protected ? TEXT.stateProtected : TEXT.stateOpen)),
                surfaceStyle: Vue.computed(() => {
                    const s = model.surface();
                    return { background: s.background, color: s.color, borderColor: s.border };
                }),
            };
        },
        template: `
<section class="clinic-dictation-protection" aria-labelledby="dpr-title">
  <h2 id="dpr-title">{{ text.title }}</h2>
  <p id="dpr-notice">{{ text.notice }}</p>
  <p aria-live="polite">{{ stateText }}</p>
  <form novalidate autocomplete="off" aria-describedby="dpr-notice"
        @submit.prevent="model.state.protected ? model.unlock() : model.enable()">
    <label>{{ text.code }}
      <input type="password" v-model="model.state.secret" autocomplete="off"
             :aria-invalid="model.state.status === 'invalid'" aria-describedby="dpr-status">
    </label>
    <label v-if="!model.state.protected">
      <input type="checkbox" v-model="model.state.confirmed"> {{ text.confirmEnable }}
    </label>
    <button v-if="model.state.protected" type="submit" :disabled="model.state.status === 'busy'">{{ text.unlock }}</button>
    <button v-else type="submit" :disabled="model.state.status === 'busy'">{{ text.enable }}</button>
  </form>
  <div v-if="model.state.protected">
    <label>
      <input type="checkbox" v-model="model.state.confirmed"> {{ text.confirmDisable }}
    </label>
    <button type="button" :disabled="model.state.status === 'busy'" @click="model.disable()">{{ text.disable }}</button>
  </div>
  <div id="dpr-status" role="status" aria-live="polite" :style="surfaceStyle">{{ model.state.message }}</div>
  <button v-if="model.state.status === 'retry'" type="button" @click="model.load()">{{ text.reload }}</button>
</section>`,
    };
}
