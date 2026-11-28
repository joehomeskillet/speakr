/**
 * In-app notifications: the badge, the list, and clearing them.
 *
 * Polled rather than pushed. The count endpoint is a single indexed COUNT and
 * the interval is slow, which is the right trade for notices that describe
 * conditions lasting minutes or hours. A socket would cost a connection per
 * open tab to deliver something nobody is waiting on.
 */

import { readJsonResponse } from '../utils/response.js';

// Slow on purpose. These are not chat messages.
const POLL_INTERVAL_MS = 60000;

export function useNotifications(state, utils) {
    const { ref } = Vue;
    const t = (key, params) => (utils && utils.t ? utils.t(key, params) : key);

    const notifications = ref([]);
    const unreadCount = ref(0);
    const isNotificationsOpen = ref(false);
    let pollTimer = null;

    const csrf = () =>
        document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');

    // Failures here are silent by design: a notification panel that cannot
    // reach the server should not raise an error banner over whatever the
    // user is actually doing.
    const refreshCount = async () => {
        try {
            const response = await fetch('/api/notifications/count');
            const data = await readJsonResponse(response, t);
            unreadCount.value = data.unread_count || 0;
        } catch (e) {
            /* leave the last known count in place */
        }
    };

    const loadNotifications = async () => {
        try {
            const response = await fetch('/api/notifications');
            const data = await readJsonResponse(response, t);
            notifications.value = data.notifications || [];
            unreadCount.value = data.unread_count || 0;
        } catch (e) {
            notifications.value = [];
        }
    };

    /** Render a stored notice through the normal i18n path. */
    const notificationText = (notification) => {
        if (!notification) return '';
        if (notification.kind === 'clinic.workflow') {
            const params = notification.params || {};
            return [params.title, params.body]
                .filter(value => typeof value === 'string' && value.length > 0).join(' — ');
        }
        return t(notification.message_key, notification.params || {});
    };

    const notificationLink = (notification) => {
        if (!notification || notification.kind !== 'clinic.workflow' || typeof notification.link !== 'string') {
            return notification?.link || null;
        }
        const match = notification.link.match(/^\/clinic\/dictations\/([A-Za-z0-9_-]{1,64})$/);
        if (!match) return notification.link;
        return `/?open=clinic-dictation&dictation=${encodeURIComponent(match[1])}`;
    };

    const toggleNotifications = async () => {
        isNotificationsOpen.value = !isNotificationsOpen.value;
        if (!isNotificationsOpen.value) return;

        await loadNotifications();
        // Opening the panel is what counts as having seen them.
        if (unreadCount.value > 0) markAllRead();
    };

    const markAllRead = async () => {
        try {
            const response = await fetch('/api/notifications/read', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
                body: JSON.stringify({}),
            });
            const data = await readJsonResponse(response, t);
            unreadCount.value = data.unread_count || 0;
            notifications.value = notifications.value.map(n => ({ ...n, read: true }));
        } catch (e) {
            /* the badge corrects itself on the next poll */
        }
    };

    const dismissNotification = async (id) => {
        try {
            const response = await fetch(`/api/notifications/${id}`, {
                method: 'DELETE',
                headers: { 'X-CSRFToken': csrf() },
            });
            const data = await readJsonResponse(response, t);
            notifications.value = notifications.value.filter(n => n.id !== id);
            unreadCount.value = data.unread_count || 0;
        } catch (e) {
            /* leave it on screen rather than lying about having cleared it */
        }
    };

    const startNotificationPolling = () => {
        if (pollTimer) return;
        refreshCount();
        pollTimer = setInterval(refreshCount, POLL_INTERVAL_MS);
    };

    const stopNotificationPolling = () => {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    };

    return {
        notifications, unreadCount, isNotificationsOpen,
        notificationText, notificationLink, toggleNotifications, loadNotifications,
        markAllRead, dismissNotification,
        startNotificationPolling, stopNotificationPolling,
    };
}
