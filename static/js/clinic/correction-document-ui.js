import { createTextHistory } from './correction-toolbar-ui.js';
import { FORMATS, useRichTextEditor } from './richtext-editor-ui.js';

const EMPTY = { schema_version: 1, nodes: [] };
const normalise = (text) => text.normalize('NFC').replace(/\r\n?/g, '\n');

function validLinks(data) {
    return (data.links || []).filter((link) => Number.isInteger(link.text_start) && Number.isInteger(link.text_end)
        && link.text_start >= 0 && link.text_end > link.text_start && link.text_end <= data.text.length
        && Number.isInteger(link.audio_start_ms) && Number.isInteger(link.audio_end_ms)
        && link.audio_start_ms >= 0 && link.audio_end_ms > link.audio_start_ms)
        .sort((a, b) => a.text_start - b.text_start);
}

function fromText(text, links = []) {
    const lines = text.split('\n');
    if (lines.length > 2048) return null;
    const cuts = [...new Set(links.flatMap((link) => [link.text_start, link.text_end]))].sort((a, b) => a - b);
    const id = globalThis.crypto.randomUUID();
    let offset = 0;
    let cut = 0;
    let runId = 0;
    const nodes = lines.map((line, index) => {
        const end = offset + line.length;
        let start = offset;
        const parts = [];
        while (cuts[cut] < end) {
            const next = cuts[cut++];
            const code = text.charCodeAt(next);
            if (next <= start || (code >= 0xdc00 && code <= 0xdfff)) continue;
            parts.push(text.slice(start, next));
            start = next;
        }
        parts.push(text.slice(start, end));
        offset = end + 1;
        return { id: `p-${id}-${index}`, kind: 'paragraph', level: 0, runs: parts.map((part) => ({
            id: `r-${id}-${runId++}`, text: part, bold: false, italic: false, underline: false, audio_anchor_id: null,
        })) };
    });
    return runId <= 8192 ? { schema_version: 1, nodes } : null;
}

function project(document) {
    const runs = new Map();
    let start = 0;
    let utf16Start = 0;
    const parts = [];
    const normalisedParts = [];
    document.nodes.forEach((node, i) => {
        if (i) {
            parts.push('\n');
            normalisedParts.push('\n');
            start += 1;
            utf16Start += 1;
        }
        for (const run of node.runs) {
            const end = start + Array.from(run.text).length;
            const normalised = normalise(run.text);
            const utf16End = utf16Start + normalised.length;
            runs.set(run.id, { run, start, end, utf16Start, utf16End });
            parts.push(run.text);
            normalisedParts.push(normalised);
            start = end;
            utf16Start = utf16End;
        }
    });
    return { runs, text: normalise(parts.join('')), normalisedParts: normalisedParts.join('') };
}

function sourcesFor(projection, data, links) {
    const sources = new Map();
    if (projection.text !== data.text || projection.normalisedParts !== data.text || !data.audio_revision_id) return sources;
    let first = 0;
    for (const [id, { run, utf16Start, utf16End }] of projection.runs) {
        if (utf16Start === utf16End) continue;
        while (links[first]?.text_end <= utf16Start) first += 1;
        const cues = [];
        for (let i = first; links[i]?.text_start < utf16End; i += 1) {
            if (links[i].text_end > utf16Start) cues.push({ start: links[i].audio_start_ms, end: links[i].audio_end_ms });
        }
        if (cues.length) sources.set(id, { text: run.text, anchor: run.audio_anchor_id, cues });
    }
    return sources;
}

function movesBetween(before, after) {
    const unchanged = [...after.keys()].filter((id) => before.get(id)?.run.text === after.get(id).run.text);
    const shared = new Set(unchanged);
    if ([...before.keys()].filter((id) => shared.has(id)).every((id, index) => id === unchanged[index])) return [];
    return unchanged.flatMap((id) => {
        const old = before.get(id);
        const next = after.get(id);
        return old.start === old.end || old.start === next.start ? [] : [{
            segment_id: id, old_start: old.start, old_end: old.end, new_start: next.start, new_end: next.end,
        }];
    });
}

function keepSources(sources, runs) {
    return new Map([...sources].filter(([id, source]) => {
        const run = runs.get(id)?.run;
        return run?.text === source.text && run.audio_anchor_id === source.anchor;
    }));
}

/** Canonical document state for the atomic correction saver and the RichTextEditor mount. */
export function useCorrectionDocument({ editor, onEdit, seek, vue }) {
    const ref = vue?.shallowRef || vue?.ref || ((value) => ({ value }));
    const computed = vue?.computed || ((get) => ({ get value() { return get(); } }));
    const rich = useRichTextEditor({ document: EMPTY, makeRef: ref });
    const projection = computed(() => project(rich.doc.value));
    const available = ref(false);
    const saved = ref({ document: EMPTY, version: 0, sources: new Map(), audioId: null });
    const readOnly = computed(() => !available.value || !editor.canEdit.value);
    const historyState = ref({ canUndo: false, canRedo: false });
    const active = ref(null);
    // The existing history compares its text slot by identity. Here it holds immutable document snapshots.
    let history = createTextHistory({ text: EMPTY });

    function reset(data, { keepDraft = false } = {}) {
        const links = validLinks(data);
        const document = data.document || fromText(data.text, links);
        // The correction API currently projects text nodes only, without the fields needed to save graphics.
        const supported = document && Array.isArray(document.nodes) && !document.nodes.some((node) => node.kind === 'graphic');
        if (keepDraft && !supported) return false;
        const version = data.document_version ?? 0;
        if (!keepDraft) {
            available.value = Boolean(supported && rich.replace(document, version));
            if (!available.value) {
                rich.replace(EMPTY, 0);
            } else if (!document.nodes.length) {
                // A persisted empty document still needs a field where the user can start typing.
                rich.replace(fromText(''), version);
            }
            history = createTextHistory({ text: rich.doc.value });
            historyState.value = { canUndo: false, canRedo: false };
        }
        const base = available.value ? document : EMPTY;
        saved.value = { document: base, version, sources: sourcesFor(project(base), data, links), audioId: data.audio_revision_id };
        active.value = null;
        return available.value;
    }

    function edited() {
        historyState.value = { canUndo: history.canUndo(), canRedo: history.canRedo() };
        onEdit(projection.value.text);
    }

    function change(operation, ...args) {
        if (readOnly.value) return false;
        const result = operation(...args);
        if (history.record({ text: rich.doc.value })) edited();
        return result;
    }

    function replaceDraft(document) {
        const { nodeId, runId } = rich.selection.value;
        rich.replace(document, saved.value.version);
        rich.select(nodeId, runId) || rich.select(nodeId, null);
    }

    function moveParagraph(step) {
        const { nodeId } = rich.selection.value;
        const nodes = rich.doc.value.nodes.slice();
        const from = nodes.findIndex((node) => node.id === nodeId);
        const to = from + step;
        if (from < 0 || ![-1, 1].includes(step) || to < 0 || to >= nodes.length) return false;
        return change(() => {
            nodes.splice(to, 0, nodes.splice(from, 1)[0]);
            replaceDraft({ ...rich.doc.value, nodes });
            return true;
        });
    }

    function restore(direction) {
        if (readOnly.value) return false;
        const entry = history[direction]();
        if (!entry) return false;
        replaceDraft(entry.text);
        edited();
        return true;
    }

    function payload() {
        if (!available.value) return null;
        const base = project(saved.value.document);
        const draft = rich.doc.value;
        const next = projection.value;
        const moveMap = movesBetween(base.runs, next.runs);
        const version = saved.value.version;
        // A legacy transcript needs a canonical base first, while its existing links are still current.
        const bootstrap = version === 0 && saved.value.sources.size > 0 && (base.text !== next.text || moveMap.length > 0);
        const document = bootstrap ? saved.value.document : {
            ...draft, nodes: draft.nodes.map((node) => ({ ...node, runs: node.runs.map((run) => {
                const prior = base.runs.get(run.id)?.run;
                return run.audio_anchor_id && (prior?.audio_anchor_id !== run.audio_anchor_id || prior.text !== run.text)
                    ? { ...run, audio_anchor_id: null } : run;
            }) })),
        };
        return { sent: bootstrap ? saved.value.document : draft, bootstrap, fields: {
            text: bootstrap ? base.text : next.text, document, expected_document_version: version,
            ...(version > 0 && moveMap.length ? { move_map: moveMap } : {}),
        } };
    }

    function acknowledge(data) {
        if (!Array.isArray(data.document?.nodes) || !Number.isInteger(data.document_version) || data.document_version < 1) return false;
        const sources = keepSources(saved.value.sources, project(data.document).runs);
        saved.value = { ...saved.value, document: data.document, version: data.document_version, sources };
        rich.version.value = data.document_version;
        return true;
    }

    const playable = computed(() => {
        const source = saved.value;
        if (!source.audioId || editor.audio?.value.revisionId !== source.audioId) return new Map();
        return keepSources(source.sources, projection.value.runs);
    });

    function seekToSegment(id) {
        const entry = playable.value.get(id);
        if (!entry || !seek) return false;
        seek(entry.cues[0].start / 1000);
        return true;
    }

    function highlightAt(seconds) {
        const ms = Number.isFinite(seconds) ? seconds * 1000 : -1;
        let runId = null;
        let latest = -1;
        for (const [id, { cues }] of playable.value) {
            for (const { start, end } of cues) {
                if (start > latest && start <= ms && ms < end) {
                    latest = start;
                    runId = id;
                }
            }
        }
        active.value = runId;
        return runId;
    }

    return {
        doc: rich.doc, version: rich.version, selection: rich.selection, announcement: rich.announcement,
        available, readOnly, historyState, select: rich.select,
        editRun: (...args) => change(rich.editRun, ...args),
        insertRun: (...args) => change(rich.insertRun, ...args),
        paste: (...args) => change(rich.paste, ...args),
        toggleFormat(format, nodeId = rich.selection.value.nodeId, runId = rich.selection.value.runId) {
            const node = rich.doc.value.nodes.find((item) => item.id === nodeId);
            return Boolean(FORMATS.includes(format) && node?.runs.some((run) => run.id === runId)
                && change(rich.toggleFormat, format, nodeId, runId));
        },
        moveParagraph, undo: () => restore('undo'), redo: () => restore('redo'),
        canSeek: (id) => playable.value.has(id), seekToSegment, highlightAt,
        activeRunId: computed(() => playable.value.has(active.value) ? active.value : null),
        reset, payload, acknowledge,
    };
}
