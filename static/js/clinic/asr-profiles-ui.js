/**
 * Source and live/batch ASR profile selection with explicit fallback state (w175).
 *
 * PAR-ASR-001, PAR-ASR-002, PAR-ASR-003, PAR-ASR-006, PAR-ASR-007, OWN-ASR-001.
 * Reuses the clinic api client (static/js/clinic/api-client.js) and the status
 * tokens (static/js/clinic/tokens.js). Server state lives only in memory:
 * nothing is written to browser storage. The server revalidates every save;
 * a stale version is reported and the state is reloaded on request.
 *
 * Server read shape (GET /asr-profiles), field names as assumed for the
 * w104 route contract:
 *   { version, profile: {id, name, version}, source: 'headset'|'recorder',
 *     sources: [...], live_profile_id, batch_profile_id, profiles: [{id, name, mode}],
 *     output_format: {container, codec, sample_rate},
 *     engine: {configured: 'gpu'|'cpu', actual: 'gpu'|'cpu', reason},
 *     prompts_enabled, wait: {offer_manual_after_s, skip_wait_minutes?, wait_disabled?},
 *     capabilities: {configured: [...], proven: [...]} }
 *
 * German labels go through the injected `t` (existing i18n mechanism, keys
 * `clinic.asr.*`); the German default applies when a key is missing.
 */

import { surfaceFor } from './tokens.js';

const BASE = '/asr-profiles';

export const SOURCES = Object.freeze(['headset', 'recorder']);
const SAVE_RULES = Object.freeze(['never', 'ask', 'always']);
const PROFILE_MODES = Object.freeze(['shared', 'separate']);
export const ENGINES = Object.freeze(['gpu', 'cpu']);
const DEFAULT_OFFER_MANUAL_AFTER_S = 120;
const CAPABILITIES = Object.freeze({
    batch: 'Batchtranskription', live: 'Live-Transkription', hotword: 'Wortschatzhinweise',
    negative_lexicon: 'Negativlexikon', pronunciation: 'Aussprachetraining',
    training: 'Akustiktraining', adaptation: 'Modellanpassung', checkpoint: 'Modellprüfpunkt',
    alignment: 'Audio-/Text-Abgleich', local_tts: 'Lokale Sprachausgabe',
    gpu_turbo: 'GPU-Erkennung', cpu_fallback: 'CPU-Ausweichlösung',
});

export function asrCapabilityLabel(value) {
    return Object.hasOwn(CAPABILITIES, value) ? CAPABILITIES[value] : 'Unbekannte Fähigkeit';
}

const LABELS = Object.freeze({
    'clinic.asr.title': 'Spracherkennung',
    'clinic.asr.profile': 'Serverprofil',
    'clinic.asr.source': 'Quelle',
    'clinic.asr.source.headset': 'Headset',
    'clinic.asr.source.recorder': 'Recorder',
    'clinic.asr.live': 'Live-Profil',
    'clinic.asr.batch': 'Batch-Profil',
    'clinic.asr.batchSource': 'Quelle für Hintergrund-Erkennung',
    'clinic.asr.profileMode': 'Quellenzuordnung',
    'clinic.asr.profileMode.shared': 'Gemeinsam',
    'clinic.asr.profileMode.separate': 'Getrennt',
    'clinic.asr.saveRule': 'Speicherregel',
    'clinic.asr.saveRule.never': 'Nie',
    'clinic.asr.saveRule.ask': 'Nach Rückfrage',
    'clinic.asr.saveRule.always': 'Immer',
    'clinic.asr.format': 'Ausgabeformat',
    'clinic.asr.engine': 'Verwendete Erkennung',
    'clinic.asr.engine.unknown': 'Nicht ermittelt',
    'clinic.asr.engine.gpu': 'GPU',
    'clinic.asr.engine.cpu': 'CPU',
    'clinic.asr.fallback': 'Ausweichlösung aktiv: CPU statt GPU.',
    'clinic.asr.prompts': 'Eingabehinweise (Prompts)',
    'clinic.asr.prompts.off': 'Aus',
    'clinic.asr.prompts.on': 'Ein',
    'clinic.asr.configured': 'Konfiguriert',
    'clinic.asr.proven': 'Nachgewiesen',
    'clinic.asr.unproven': 'Nicht nachgewiesen',
    'clinic.asr.manual': 'Manuell schreiben',
    'clinic.asr.wait': 'Wartezeit (Minuten)',
    'clinic.asr.save': 'Übernehmen',
    'clinic.asr.cancel': 'Abbrechen',
    'clinic.asr.retry': 'Erneut versuchen',
    'clinic.asr.reload': 'Neu laden',
    'clinic.asr.saved': 'Gespeichert.',
    'clinic.asr.removed': 'Die gewählte Auswahl ist nicht mehr verfügbar.',
});

const ADMIN_LABELS = Object.freeze({
    'clinic.asr.profile': 'Gespeichertes Sprachprofil',
    'clinic.asr.source': 'Gerät zum Diktieren',
    'clinic.asr.live': 'Profil beim Diktieren (live)',
    'clinic.asr.batch': 'Profil für nachträgliche Erkennung',
    'clinic.asr.wait': 'Wartezeit bis zum manuellen Schreiben (Minuten)',
    'clinic.asr.save': 'Profilvorgabe übernehmen',
    'clinic.asr.saved': 'Profilvorgabe gespeichert.',
});

const SERVER_MESSAGES = Object.freeze({
    stale_version: 'Der Stand ist veraltet. Bitte neu laden.',
    profile_not_found: 'Das Profil ist nicht mehr verfügbar.',
});

function str(value) {
    return typeof value === 'string' ? value : '';
}

// Stored minutes win. Otherwise a deadline that is a whole number of minutes.
function minutesOf(wait) {
    const stored = wait.skip_wait_minutes;
    if (Number.isInteger(stored) && stored >= 1) return stored;
    const seconds = wait.offer_manual_after_s;
    if (Number.isInteger(seconds) && seconds >= 60 && seconds % 60 === 0) return seconds / 60;
    return DEFAULT_OFFER_MANUAL_AFTER_S / 60;
}

/**
 * @param {{client: {request: Function, switchContext: Function},
 *          t?: (key: string) => string, adminLayout?: boolean}} deps
 */
export function createAsrProfilesController(deps) {
    const client = deps.client;
    const translate = typeof deps.t === 'function' ? deps.t : null;
    const listeners = new Set();

    function label(key) {
        const text = translate ? translate(key) : key;
        return text && text !== key ? text : ((deps.adminLayout && ADMIN_LABELS[key]) || LABELS[key] || key);
    }

    function emptyServer() {
        return {
            version: 0, profile: null, source: 'headset', liveProfileId: null, batchProfileId: null,
            saveRule: 'never', profileMode: null, batchSource: null,
            profiles: [], output: null, engine: { configured: 'gpu', actual: 'gpu', reason: '' },
            promptsEnabled: false, offerManualAfterS: DEFAULT_OFFER_MANUAL_AFTER_S,
            waitDisabled: false, skipWaitMinutes: DEFAULT_OFFER_MANUAL_AFTER_S / 60,
            configured: [], proven: [],
        };
    }

    function draftOf({ source, liveProfileId, batchProfileId, skipWaitMinutes, saveRule, profileMode, batchSource }) {
        return { source, liveProfileId, batchProfileId, skipWaitMinutes, saveRule, profileMode, batchSource };
    }

    const state = {
        ready: false,
        loading: false,
        busy: false,
        error: null, // { outcome, code, message, retryable }
        notice: null,
        server: emptyServer(),
        draft: draftOf(emptyServer()),
    };

    function emit() {
        for (const fn of listeners) fn(state);
    }

    function fail(res) {
        const code = res.code || null;
        state.error = {
            outcome: res.outcome, code,
            message: SERVER_MESSAGES[code] || res.message,
            retryable: res.outcome === 'network' || res.outcome === 'UNKNOWN',
        };
        return false;
    }

    function offered(mode) {
        return state.server.profiles.filter((p) => p.mode === mode || p.mode === 'both').map((p) => p.id);
    }

    // Selections the server no longer offers are dropped from the draft.
    function revalidate() {
        const d = state.draft;
        let removed = false;
        if (d.liveProfileId !== null && !offered('live').includes(d.liveProfileId)) {
            d.liveProfileId = null;
            removed = true;
        }
        if (d.batchProfileId !== null && !offered('batch').includes(d.batchProfileId)) {
            d.batchProfileId = null;
            removed = true;
        }
        if (removed) state.notice = label('clinic.asr.removed');
    }

    async function load() {
        state.ready = false;
        state.loading = true;
        state.error = null;
        state.notice = null;
        emit();
        const res = await client.request(BASE);
        state.loading = false;
        if (res.outcome !== 'ok') {
            fail(res);
            emit();
            return false;
        }
        const d = res.data || {};
        const engine = d.engine || {};
        const caps = d.capabilities || {};
        const wait = d.wait || {};
        const base = emptyServer();
        state.server = {
            version: d.version || 0,
            profile: d.profile && typeof d.profile === 'object' ? d.profile : null,
            source: SOURCES.includes(d.source) ? d.source : base.source,
            saveRule: SAVE_RULES.includes(d.save_rule) ? d.save_rule : base.saveRule,
            supportsSaveRule: SAVE_RULES.includes(d.save_rule),
            profileMode: PROFILE_MODES.includes(d.profile_mode) ? d.profile_mode : null,
            batchSource: d.profile_mode === 'separate' && SOURCES.includes(d.batch_source) ? d.batch_source : null,
            liveProfileId: d.live_profile_id == null ? null : String(d.live_profile_id),
            batchProfileId: d.batch_profile_id == null ? null : String(d.batch_profile_id),
            profiles: Array.isArray(d.profiles)
                ? d.profiles.map((p) => ({ id: String(p.id), name: str(p.name), mode: str(p.mode) })) : [],
            output: d.output_format && typeof d.output_format === 'object' ? d.output_format : null,
            engine: {
                configured: ENGINES.includes(engine.configured) ? engine.configured : base.engine.configured,
                actual: ENGINES.includes(engine.actual) ? engine.actual : base.engine.actual,
                reason: str(engine.reason),
            },
            // Prompts stay off unless the server says otherwise.
            promptsEnabled: d.prompts_enabled === true,
            offerManualAfterS: Number.isInteger(wait.offer_manual_after_s) && wait.offer_manual_after_s >= 0
                ? wait.offer_manual_after_s : DEFAULT_OFFER_MANUAL_AFTER_S,
            waitDisabled: wait.wait_disabled === true,
            skipWaitMinutes: minutesOf(wait),
            configured: Array.isArray(caps.configured) ? caps.configured.map(String) : [],
            proven: Array.isArray(caps.proven) ? caps.proven.map(String) : [],
        };
        state.draft = draftOf(state.server);
        state.ready = true;
        revalidate();
        emit();
        return true;
    }

    function setSource(source) {
        if (!SOURCES.includes(source)) return false;
        state.draft.source = source;
        emit();
        return true;
    }

    function setSaveRule(value) {
        if (!SAVE_RULES.includes(value)) return false;
        state.draft.saveRule = value;
        emit();
        return true;
    }

    function setProfileMode(value) {
        if (!PROFILE_MODES.includes(value)) return false;
        const d = state.draft;
        d.profileMode = value;
        d.batchSource = value === 'separate' ? (d.batchSource ?? d.source) : null;
        emit();
        return true;
    }

    function setBatchSource(value) {
        if (!SOURCES.includes(value) || state.draft.profileMode !== 'separate') return false;
        state.draft.batchSource = value;
        emit();
        return true;
    }

    function setSkipWaitMinutes(value) {
        const text = String(value == null ? '' : value).trim();
        if (!/^[1-9]\d*$/.test(text)) return false;
        const minutes = Number(text);
        if (!Number.isInteger(minutes) || minutes < 1) return false;
        state.draft.skipWaitMinutes = minutes;
        emit();
        return true;
    }

    function setProfile(mode, id) {
        if (mode !== 'live' && mode !== 'batch') return false;
        if (id !== null && !offered(mode).includes(String(id))) return false;
        state.draft[mode === 'live' ? 'liveProfileId' : 'batchProfileId'] = id === null ? null : String(id);
        emit();
        return true;
    }

    async function save() {
        if (!state.ready || state.loading || state.busy) return false;
        const d = state.draft;
        state.busy = true;
        state.error = null;
        emit();
        const body = {
            source: d.source, live_profile_id: d.liveProfileId, batch_profile_id: d.batchProfileId,
            expected_version: state.server.version,
        };
        if (d.skipWaitMinutes !== state.server.skipWaitMinutes) body.skip_wait_minutes = d.skipWaitMinutes;
        if (d.saveRule !== state.server.saveRule) body.save_rule = d.saveRule;
        if (d.profileMode !== state.server.profileMode || d.batchSource !== state.server.batchSource) {
            body.profile_mode = d.profileMode;
            if (d.profileMode === 'separate') body.batch_source = d.batchSource;
        }
        const res = await client.request(BASE + '/selection', {
            method: 'PUT',
            body,
        });
        state.busy = false;
        if (res.outcome !== 'ok') {
            fail(res);
            emit();
            return false;
        }
        const loaded = await load();
        if (loaded) { state.notice = label('clinic.asr.saved'); emit(); }
        return loaded;
    }

    function cancel() {
        state.draft = draftOf(state.server);
        state.error = null;
        emit();
    }

    // Account or dictation change: abort in-flight calls, drop all state, reload.
    async function switchContext(next) {
        const n = next || {};
        client.switchContext({ accountId: n.accountId, dictationId: n.dictationId });
        state.server = emptyServer();
        state.draft = draftOf(emptyServer());
        return load();
    }

    // The manual-correction action appears after the configured wait, or at
    // once when ASR failed or was skipped. It never depends on ASR finishing.
    function manualAction(asrStatus, waitedS) {
        const failed = asrStatus === 'failed' || asrStatus === 'skipped';
        const waiting = asrStatus === 'pending' || asrStatus === 'running';
        if (state.server.waitDisabled && waiting) {
            return { visible: false, label: label('clinic.asr.manual') };
        }
        const waited = Number.isInteger(waitedS) && waitedS >= state.server.offerManualAfterS;
        return { visible: failed || (waiting && waited), label: label('clinic.asr.manual') };
    }

    function view(asrStatus, waitedS) {
        const s = state.server;
        const d = state.draft;
        const fallback = s.engine.configured === 'gpu' && s.engine.actual === 'cpu';
        const provenSet = new Set(s.proven);
        const verifiedEngine = ['batch', 'live'].some((name) => provenSet.has(name)) || fallback;
        const dirty = d.source !== s.source || d.liveProfileId !== s.liveProfileId || d.batchProfileId !== s.batchProfileId
            || d.skipWaitMinutes !== s.skipWaitMinutes || d.saveRule !== s.saveRule
            || d.profileMode !== s.profileMode || d.batchSource !== s.batchSource;
        const complete = d.profileMode !== 'separate' || SOURCES.includes(d.batchSource);
        const notice = deps.adminLayout && dirty && state.notice === label('clinic.asr.saved') ? null : state.notice;
        const choices = (mode) => offered(mode).map((id) => ({
            value: id,
            label: (s.profiles.find((p) => p.id === id) || {}).name || id,
            selected: d[mode === 'live' ? 'liveProfileId' : 'batchProfileId'] === id,
        }));
        return {
            title: label('clinic.asr.title'),
            ready: state.ready, loading: state.loading, busy: state.busy,
            canSave: state.ready && !state.loading && !state.busy && complete
                && (dirty || (deps.adminLayout === true && !s.profile)),
            profileMode: d.profileMode,
            profileModes: PROFILE_MODES.map((v) => ({ value: v, label: label('clinic.asr.profileMode.' + v) })),
            batchSource: d.batchSource,
            saveRule: d.saveRule,
            supportsSaveRule: s.supportsSaveRule,
            saveRules: SAVE_RULES.map((v) => ({ value: v, label: label('clinic.asr.saveRule.' + v) })),
            dirty,
            profile: s.profile ? { id: str(s.profile.id), name: str(s.profile.name), version: s.profile.version } : null,
            sources: SOURCES.map((v) => ({ value: v, label: label('clinic.asr.source.' + v), selected: d.source === v })),
            live: choices('live'),
            batch: choices('batch'),
            output: s.output ? {
                container: str(s.output.container), codec: str(s.output.codec), sampleRate: s.output.sample_rate,
            } : null,
            engine: {
                configured: s.engine.configured,
                actual: s.engine.actual,
                label: label('clinic.asr.engine.' + (verifiedEngine ? s.engine.actual : 'unknown')),
                fallback,
                fallbackLabel: fallback ? label('clinic.asr.fallback') : '',
                reason: s.engine.reason,
                surface: surfaceFor(fallback ? 'warning' : (verifiedEngine ? 'success' : 'normal')),
            },
            prompts: { enabled: s.promptsEnabled, label: label('clinic.asr.prompts.' + (s.promptsEnabled ? 'on' : 'off')) },
            capabilities: s.configured.map((name) => ({
                name,
                proven: provenSet.has(name),
                label: provenSet.has(name) ? label('clinic.asr.proven') : label('clinic.asr.unproven'),
            })),
            skipWaitMinutes: d.skipWaitMinutes,
            waitDisabled: s.waitDisabled,
            manual: manualAction(asrStatus, waitedS),
            error: state.error,
            errorSurface: state.error ? surfaceFor('error') : null,
            notice,
            a11y: {
                role: 'form',
                sourceGroup: { role: 'radiogroup', label: label('clinic.asr.source') },
                alert: { role: 'alert', live: 'assertive' },
                status: { role: 'status', live: 'polite' },
                keys: { select: ['Enter', ' '], move: ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'], cancel: ['Escape'] },
            },
            labels: {
                profile: label('clinic.asr.profile'), source: label('clinic.asr.source'),
                live: label('clinic.asr.live'), batch: label('clinic.asr.batch'),
                profileMode: label('clinic.asr.profileMode'), batchSource: label('clinic.asr.batchSource'),
                saveRule: label('clinic.asr.saveRule'),
                format: label('clinic.asr.format'), engine: label('clinic.asr.engine'),
                prompts: label('clinic.asr.prompts'), wait: label('clinic.asr.wait'),
                configured: label('clinic.asr.configured'), proven: label('clinic.asr.proven'),
                save: label('clinic.asr.save'), cancel: label('clinic.asr.cancel'),
                retry: label('clinic.asr.retry'), reload: label('clinic.asr.reload'),
            },
        };
    }

    // Arrow keys move within the source radio group (wraps); Escape cancels.
    function onKeydown(event) {
        const key = event && event.key;
        if (key === 'Escape') {
            cancel();
            return true;
        }
        if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(key)) return false;
        const step = key === 'ArrowUp' || key === 'ArrowLeft' ? -1 : 1;
        const next = SOURCES[(SOURCES.indexOf(state.draft.source) + step + SOURCES.length) % SOURCES.length];
        return setSource(next);
    }

    return {
        state, view, load, setSource, setProfile, setProfileMode, setBatchSource, setSaveRule,
        setSkipWaitMinutes, save, cancel, switchContext, onKeydown,
        subscribe(fn) {
            listeners.add(fn);
            return () => listeners.delete(fn);
        },
    };
}

/** Vue composable: reactive snapshot of the controller view (needs window.Vue). */
export function useAsrProfiles(deps) {
    const controller = createAsrProfilesController(deps);
    const Vue = typeof window !== 'undefined' ? window.Vue : null;
    if (!Vue) return { controller, view: null };
    const view = Vue.shallowRef(controller.view());
    controller.subscribe(() => { view.value = controller.view(); });
    return { controller, view };
}
