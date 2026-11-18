// Clinic status surfaces bound to semantic variables in static/css/styles.css.
// A more specific clinic color-token owner decision was not located.
// This module invents no palette and hard-codes no colours.

export const TOKEN_CONTRACT_VERSION = 1;

export const COLOR_TOKEN_DECISION = Object.freeze({
    located: false,
    source: 'static/css/styles.css',
    themes: Object.freeze(['light', 'dark']),
    note: 'A more specific clinic color-token owner decision was not located. No palette was invented.',
});

// Same keyboard ring as the primitive in static/css/primitives.css
// (button:focus-visible). --border-focus is set on :root and on .dark,
// so the ring stays visible in both themes without using the status fill.
export const FOCUS_INDICATOR = Object.freeze({
    applies: ':focus-visible',
    outline: '2px solid var(--border-focus)',
    outlineOffset: '2px',
});

// Border tokens that exist for both themes are --border-primary,
// --border-secondary, --border-accent, --border-danger and --border-focus.
// Pending, processing, success and warning have no dedicated border
// variable in both themes, so they use --border-primary.
const SURFACES = Object.freeze({
    pending: surface('pending', 'Ausstehend', '--bg-pending-light', '--text-pending-strong', '--border-primary'),
    processing: surface('processing', 'Verarbeitung', '--bg-processing-light', '--text-processing-strong', '--border-primary'),
    success: surface('success', 'Abgeschlossen', '--bg-success-light', '--text-success-strong', '--border-primary'),
    warning: surface('warning', 'Warnung', '--bg-warn-light', '--text-warn-strong', '--border-primary'),
    error: surface('error', 'Fehlgeschlagen', '--bg-danger-light', '--text-danger-strong', '--border-danger'),
    normal: surface('normal', 'Normal', '--bg-secondary', '--text-primary', '--border-primary'),
});

export const STATUSES = Object.freeze(Object.keys(SURFACES));

// Legacy recording statuses keep their previous meaning.
const ALIASES = Object.freeze({
    pending: 'pending',
    queued: 'pending',
    processing: 'processing',
    transcribing: 'processing',
    uploading: 'processing',
    success: 'success',
    completed: 'success',
    done: 'success',
    warning: 'warning',
    warn: 'warning',
    summarizing: 'warning',
    error: 'error',
    failed: 'error',
    failure: 'error',
    normal: 'normal',
});

export class TokenContractError extends Error {
    constructor(code) {
        super(code);
        this.name = 'TokenContractError';
        this.code = code;
    }
}

function surface(status, label, background, color, border) {
    return Object.freeze({
        status,
        label,
        background: `var(${background})`,
        color: `var(${color})`,
        border: `var(${border})`,
        tokens: Object.freeze({ background, color, border }),
        focus: FOCUS_INDICATOR,
    });
}

function canonical(status) {
    if (typeof status !== 'string') return null;
    return ALIASES[status.trim().toLowerCase()] || null;
}

/**
 * Resolve one status surface.
 * `authorized: false` refuses without a colour (unauthorized).
 * A version other than TOKEN_CONTRACT_VERSION refuses (stale_version).
 * An unknown status refuses (failure) and does not invent a colour.
 * A repeated call with the same status is a retry and returns the same tokens.
 */
export function surfaceFor(status, context = {}) {
    if (context.authorized === false) {
        throw new TokenContractError('unauthorized');
    }
    if (context.version != null && context.version !== TOKEN_CONTRACT_VERSION) {
        throw new TokenContractError('stale_version');
    }
    const key = canonical(status);
    if (!key) {
        throw new TokenContractError('failure');
    }
    return SURFACES[key];
}
