/** Searchable select fields (Tom Select). Re-runs after HTMX swaps so dynamic formset rows work. */
import TomSelect from 'tom-select';
import 'tom-select/dist/css/tom-select.css';
// Theme overrides must load after Tom Select base CSS
import '../styles/app/searchable-select.css';

const SELECTOR = 'select:not([data-no-search]):not(.tomselected)';

function enhanceSelect(el) {
  if (!(el instanceof HTMLSelectElement)) {
    return;
  }
  if (el.tomselect || el.dataset.noSearch !== undefined) {
    return;
  }
  // Ignore empty shell selects that appear before options are filled
  if (el.options.length === 0) {
    return;
  }

  const isMultiple = el.multiple;
  const placeholder =
    el.getAttribute('placeholder') ||
    el.dataset.placeholder ||
    (el.options[0] && el.options[0].value === '' ? el.options[0].text : 'Search…');

  new TomSelect(el, {
    create: false,
    allowEmptyOption: true,
    maxOptions: null,
    maxItems: isMultiple ? null : 1,
    hideSelected: isMultiple,
    closeAfterSelect: !isMultiple,
    placeholder,
    plugins: {
      dropdown_input: {},
      ...(isMultiple ? { remove_button: { title: 'Remove' } } : {}),
    },
    render: {
      no_results: (_data, escape) =>
        `<div class="no-results">No results for "${escape(_data.input)}"</div>`,
    },
  });
}

export function initSearchableSelects(root = document) {
  if (!root) {
    return;
  }
  if (root instanceof HTMLSelectElement) {
    enhanceSelect(root);
    return;
  }
  if (root.matches?.(SELECTOR)) {
    enhanceSelect(root);
  }
  root.querySelectorAll?.(SELECTOR).forEach(enhanceSelect);
}

function boot() {
  initSearchableSelects(document);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}

document.body.addEventListener('htmx:afterSettle', () => {
  initSearchableSelects(document);
});
