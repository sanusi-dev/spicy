import { trapFocusInPanel } from './focus';

document.addEventListener('alpine:init', () => {
  const ENTER_MS = 200;
  const LEAVE_MS = 150;

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
});
