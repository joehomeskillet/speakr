// Password show/hide toggle for the clinic login page (V8 P20).
// A plain button: Enter and Space activate it natively, focus never leaves it.
(function () {
    'use strict';

    var LABEL_SHOW = 'Passwort anzeigen';
    var LABEL_HIDE = 'Passwort verbergen';

    function toggle(button) {
        var input = document.getElementById(button.getAttribute('data-password-toggle'));
        if (!input) return;
        var show = input.type === 'password';
        input.type = show ? 'text' : 'password';
        button.setAttribute('aria-pressed', show ? 'true' : 'false');
        button.setAttribute('aria-label', show ? LABEL_HIDE : LABEL_SHOW);
        var icon = button.querySelector('i');
        if (icon) icon.className = show ? 'fas fa-eye-slash' : 'fas fa-eye';
    }

    document.addEventListener('click', function (event) {
        var button = event.target.closest && event.target.closest('[data-password-toggle]');
        if (button) toggle(button);
    });
})();
