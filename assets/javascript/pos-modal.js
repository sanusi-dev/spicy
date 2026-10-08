import { FOCUSABLE, trapFocusInPanel } from './focus';

document.addEventListener('alpine:init', () => {
  // Shared by the payment, add-on, variant, and cash-out dialogs.
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
