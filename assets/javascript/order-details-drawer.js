document.addEventListener('alpine:init', () => {
  const FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  const ENTER_MS = 200;
  const LEAVE_MS = 150;

  function trapFocusInPanel(panel, event) {
    if (event.key !== 'Tab' || !panel) {
      return;
    }
    const focusable = Array.from(panel.querySelectorAll(FOCUSABLE));
    if (!focusable.length) {
      event.preventDefault();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function afterPaint(callback) {
    // Two rAFs so the off-screen enter-start paints before we open.
    requestAnimationFrame(() => {
      requestAnimationFrame(callback);
    });
  }

  Alpine.data('orderDetailsDrawer', (returnFocusId, options = {}) => {
    // animate:false for re-swaps (print) — open immediately, no enter transition.
    const animate = options.animate !== false;
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    return {
      open: !animate || reducedMotion,
      returnFocusId,

      init() {
        if (!animate || reducedMotion) {
          this.open = true;
          this.$nextTick(() => this.$refs.closeButton?.focus());
          return;
        }

        // Open after x-cloak clears and the closed frame paints, so the slide runs on the compositor.
        afterPaint(() => {
          this.open = true;
        });
        // Focus only after the slide finishes (focus mid-transition causes jank).
        window.setTimeout(() => this.$refs.closeButton?.focus(), ENTER_MS);
      },

      close() {
        if (!this.open) {
          return;
        }

        this.open = false;
        const delay = reducedMotion ? 0 : LEAVE_MS;

        window.setTimeout(() => {
          // The row clears its selected state first so the next open starts with clean ARIA state.
          this.$dispatch('order-details-closed');
          this.$root.remove();
          document.getElementById(this.returnFocusId)?.focus();
        }, delay);
      },

      trapFocus(event) {
        trapFocusInPanel(this.$refs.panel, event);
      },
    };
  });

  Alpine.data('posModalDialog', (options = {}) => ({
    open: true,
    closeUrl: options.closeUrl || '',
    removeOnClose: Boolean(options.removeOnClose),
    selected: options.selected || [],
    previousFocus: null,

    init() {
      this.previousFocus = document.activeElement;
      this.$nextTick(() => {
        const focusTarget = this.$refs.closeButton || this.$refs.panel?.querySelector(FOCUSABLE);
        focusTarget?.focus();
      });
    },

    close() {
      if (!this.open) {
        return;
      }
      this.open = false;
      if (this.closeUrl) {
        window.location.href = this.closeUrl;
        return;
      }
      if (this.removeOnClose) {
        this.$root.remove();
      }
      if (this.previousFocus && typeof this.previousFocus.focus === 'function') {
        this.previousFocus.focus();
      }
    },

    trapFocus(event) {
      trapFocusInPanel(this.$refs.panel, event);
    },
  }));
});
