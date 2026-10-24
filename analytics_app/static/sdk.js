/* Gather browser SDK v1. No dependencies. Call destroy() when retiring an instance. */
(function (root) {
  'use strict';
  class GatherAnalytics {
    constructor({ endpoint = '/api/v1/events', storageKey = 'gather', enabled = true, autoPageViews = true } = {}) {
      this.endpoint = endpoint;
      this.key = storageKey;
      this.enabled = enabled;
      this.queue = [];
      this.failures = 0;
      this.nextAttempt = 0;
      this.inflight = null;
      this.destroyed = false;
      this.dropped = 0;
      this.storageAvailable = true;
      try {
        this.anonymousId = localStorage.getItem(`${this.key}:visitor`) || crypto.randomUUID();
        localStorage.setItem(`${this.key}:visitor`, this.anonymousId);
        const stored = JSON.parse(localStorage.getItem(`${this.key}:queue`) || '[]');
        if (Array.isArray(stored)) this.queue = stored.filter(e => Date.now() - Date.parse(e.occurred_at) < 7 * 86400000).slice(-200);
      } catch (_) { this.storageAvailable = false; this.anonymousId = crypto.randomUUID(); }
      this.sessionId = crypto.randomUUID();
      this.touchSession();
      this.onVisibility = () => { if (document.visibilityState === 'hidden') this.flush(true); };
      this.onOnline = () => { this.nextAttempt = 0; this.flush(); };
      this.onNavigation = () => { if (autoPageViews) this.page(); };
      document.addEventListener('visibilitychange', this.onVisibility);
      root.addEventListener('online', this.onOnline);
      root.addEventListener('popstate', this.onNavigation);
      if (autoPageViews) {
        this.originalPush = history.pushState;
        this.originalReplace = history.replaceState;
        const sdk = this;
        this.pushWrapper = function (...args) { sdk.originalPush.apply(this, args); sdk.page(); };
        this.replaceWrapper = function (...args) { sdk.originalReplace.apply(this, args); sdk.page(); };
        history.pushState = this.pushWrapper;
        history.replaceState = this.replaceWrapper;
        this.page();
      }
      this.timer = setInterval(() => this.flush(), 3000);
      if (enabled) this.flush();
    }
    touchSession() {
      const now = Date.now();
      try {
        const session = JSON.parse(sessionStorage.getItem(`${this.key}:session`) || 'null');
        if (session && now - session.at < 1800000) this.sessionId = session.id;
        else this.sessionId = crypto.randomUUID();
        sessionStorage.setItem(`${this.key}:session`, JSON.stringify({ id: this.sessionId, at: now }));
      } catch (_) { /* In-memory identity still works when storage is blocked. */ }
    }
    persist() {
      try { localStorage.setItem(`${this.key}:queue`, JSON.stringify(this.queue)); }
      catch (_) { this.storageAvailable = false; }
    }
    identify(userId) { this.userId = userId; }
    track(name, properties = {}) {
      if (!this.enabled || this.destroyed) return null;
      if (!/^[a-z][a-z0-9_]{0,63}$/.test(name)) throw new Error('Invalid event name');
      const serialized = JSON.stringify(properties);
      if (!properties || Array.isArray(properties) || typeof properties !== 'object' || new TextEncoder().encode(serialized).length > 8192)
        throw new Error('Properties must be a JSON object up to 8 KiB');
      if (/"(?:email|password|token|authorization|phone|address)"\s*:/i.test(serialized)) throw new Error('Personal data is not allowed');
      this.touchSession();
      const event = { event_id: crypto.randomUUID(), name, anonymous_id: this.anonymousId,
        session_id: this.sessionId, occurred_at: new Date().toISOString(), path: location.pathname,
        properties: JSON.parse(serialized) };
      if (this.userId) event.user_id = this.userId;
      if (this.queue.length >= 200) { this.queue.shift(); this.dropped++; }
      this.queue.push(event);
      this.persist();
      if (this.queue.length >= 10) this.flush();
      return event.event_id;
    }
    page(properties = {}) { return this.track('page_view', properties); }
    async flush(unloading = false) {
      if (!this.enabled || this.destroyed || !this.queue.length || this.inflight || Date.now() < this.nextAttempt) return;
      // Bound keepalive payloads well below the shared browser 64 KiB budget.
      const batch = [];
      for (const event of this.queue.slice(0, 20)) {
        if (new TextEncoder().encode(JSON.stringify({ events: [...batch, event] })).length > 48000) break;
        batch.push(event);
      }
      if (!batch.length) { this.queue.shift(); this.dropped++; this.persist(); return; }
      const ids = new Set(batch.map(e => e.event_id));
      this.inflight = (async () => {
        try {
          const response = await fetch(this.endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ events: batch }), keepalive: unloading, signal: AbortSignal.timeout(5000) });
          if (!response.ok) {
            if (response.status >= 400 && response.status < 500 && ![408, 429].includes(response.status)) {
              this.queue = this.queue.filter(e => !ids.has(e.event_id));
              this.dropped += batch.length;
              this.persist();
            }
            throw new Error(`Analytics HTTP ${response.status}`);
          }
          this.queue = this.queue.filter(e => !ids.has(e.event_id));
          this.persist();
          this.failures = 0;
          this.nextAttempt = 0;
        } catch (_) {
          this.failures++;
          this.nextAttempt = Date.now() + Math.min(60000, 1000 * 2 ** Math.min(this.failures, 6)) + Math.random() * 500;
        }
      })();
      await this.inflight;
      this.inflight = null;
    }
    setEnabled(enabled) {
      this.enabled = Boolean(enabled);
      if (!this.enabled) { this.queue = []; this.persist(); }
    }
    destroy() {
      clearInterval(this.timer);
      document.removeEventListener('visibilitychange', this.onVisibility);
      root.removeEventListener('online', this.onOnline);
      root.removeEventListener('popstate', this.onNavigation);
      if (history.pushState === this.pushWrapper) history.pushState = this.originalPush;
      if (history.replaceState === this.replaceWrapper) history.replaceState = this.originalReplace;
      this.destroyed = true;
    }
  }
  root.GatherAnalytics = GatherAnalytics;
})(window);
