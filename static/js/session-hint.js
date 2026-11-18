// Expired-session hint.
//
// When the session ends, API calls from the page get a 401, a redirect to the
// login form, or the login page's HTML instead of JSON. Without this the UI
// just shows broken states. csrf-refresh.js's fetch interceptor is the one
// place every API call passes through; it hands each response to
// window.sessionHint.inspect(), which shows one fixed banner with a reload
// button. It never reloads by itself, so unsaved input is not discarded.
(function () {
    'use strict';

    var shown = false;
    var BANNER_ID = 'session-expired-banner';

    function translate(key, fallback) {
        try {
            if (window.i18n && typeof window.i18n.t === 'function') {
                var value = window.i18n.t('errors.' + key);
                if (value && value !== 'errors.' + key && value !== key) return value;
            }
        } catch (e) { /* fall through to the default text */ }
        return fallback;
    }

    function isLoginUrl(url) {
        try {
            return /^\/(login|auth\/login)(\/|$)/.test(new URL(url, window.location.href).pathname);
        } catch (e) {
            return false;
        }
    }

    function isApiRequest(url) {
        return typeof url === 'string' && (
            url.indexOf('/api/') === 0 || url.indexOf('/upload') === 0 || url.indexOf('/save') === 0 ||
            url.indexOf('/recording/') === 0 || url.indexOf('/chat') === 0 || url.indexOf('/speakers') === 0);
    }

    /** True when the response means "your session is gone", not "the app said no". */
    function isSessionLost(url, response) {
        if (!response || !isApiRequest(url) || isLoginUrl(url)) return false;
        if (response.status === 401) return true;
        if (response.redirected && isLoginUrl(response.url)) return true;
        var type = (response.headers && response.headers.get('content-type')) || '';
        // An API call that answers 200 with an HTML page was sent to the login form.
        // Only /api/ is JSON-only; other prefixes may serve HTML legitimately.
        return url.indexOf('/api/') === 0 && response.ok && type.indexOf('text/html') !== -1;
    }

    function show() {
        if (shown || typeof document === 'undefined' || !document.body) return;
        shown = true;

        var banner = document.createElement('div');
        banner.id = BANNER_ID;
        banner.setAttribute('role', 'alert');
        banner.setAttribute('aria-live', 'assertive');
        banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483000;' +
            'display:flex;gap:12px;align-items:center;justify-content:center;flex-wrap:wrap;' +
            'padding:12px 16px;background:#b45309;color:#fff;font:500 14px/1.4 system-ui,sans-serif;' +
            'box-shadow:0 2px 8px rgba(0,0,0,.3);';

        var text = document.createElement('span');
        text.setAttribute('data-session-hint-message', '');
        text.textContent = translate('sessionHintMessage',
            'Sitzung abgelaufen. Bitte laden Sie die Seite neu. Ungespeicherte Eingaben bleiben bis dahin sichtbar.');

        var button = document.createElement('button');
        button.type = 'button';
        button.setAttribute('data-session-hint-reload', '');
        button.textContent = translate('sessionHintReload', 'Seite neu laden');
        button.style.cssText = 'padding:6px 14px;border:0;border-radius:6px;background:#fff;' +
            'color:#7c2d12;font:600 14px system-ui,sans-serif;cursor:pointer;';
        button.addEventListener('click', function () { window.location.reload(); });

        banner.appendChild(text);
        banner.appendChild(button);
        document.body.appendChild(banner);
    }

    window.sessionHint = {
        inspect: function (url, response) {
            if (isSessionLost(url, response)) show();
        },
        isSessionLost: isSessionLost,
    };
})();
