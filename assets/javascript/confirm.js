import Swal from 'sweetalert2';

function confirmMessage(element) {
  return element.dataset.confirmMessage || element.dataset.confirmBody || '';
}

function confirmTitle(element) {
  return element.dataset.confirmTitle || 'Are you sure?';
}

function confirmButtonText(element) {
  return element.dataset.confirmBtn || element.dataset.confirmButton || 'Confirm';
}

function showConfirm(element) {
  return Swal.fire({
    title: confirmTitle(element),
    html: confirmMessage(element),
    icon: element.dataset.confirmIcon || 'question',
    showCancelButton: true,
    confirmButtonColor: element.dataset.confirmColor || undefined,
    confirmButtonText: confirmButtonText(element),
    cancelButtonText: 'Cancel',
  });
}

// Custom data-confirm-* dialogs; native hx-confirm values remain handled by HTMX.
document.addEventListener('htmx:confirm', function (e) {
  const message = confirmMessage(e.target);
  if (!message && !e.target.dataset.confirmTitle) {
    return;
  }

  e.preventDefault();

  showConfirm(e.target).then(function (result) {
    if (result.isConfirmed) {
      e.detail.issueRequest(true);
    }
  });
});

// A one-shot data-confirm-accepted flag lets the re-submitted event pass through without prompting twice.
document.addEventListener(
  'submit',
  function (e) {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    if (!confirmMessage(form) && !form.dataset.confirmTitle) {
      return;
    }
    if (form.dataset.confirmAccepted === 'true') {
      delete form.dataset.confirmAccepted;
      return;
    }
    e.preventDefault();
    showConfirm(form).then(function (result) {
      if (result.isConfirmed) {
        form.dataset.confirmAccepted = 'true';
        form.requestSubmit();
      }
    });
  },
  true,
);

document.addEventListener(
  'click',
  function (e) {
    const trigger = e.target.closest('[data-confirm-message], [data-confirm-body], [data-confirm-title]');
    if (!trigger || trigger.closest('form')) {
      return;
    }
    if (!(trigger instanceof HTMLElement)) {
      return;
    }
    if (
      trigger.hasAttribute('hx-get') ||
      trigger.hasAttribute('hx-post') ||
      trigger.hasAttribute('hx-put') ||
      trigger.hasAttribute('hx-patch') ||
      trigger.hasAttribute('hx-delete')
    ) {
      return;
    }
    if (!confirmMessage(trigger) && !trigger.dataset.confirmTitle) {
      return;
    }
    e.preventDefault();
    showConfirm(trigger).then(function (result) {
      if (!result.isConfirmed) {
        return;
      }
      if (trigger.tagName === 'A' && trigger.href) {
        window.location.href = trigger.href;
        return;
      }
      if (typeof trigger.click === 'function') {
        // One-shot flag so the synthetic click below doesn't re-trigger this handler.
        trigger.dataset.confirmAccepted = 'true';
        trigger.click();
      }
    });
  },
  true,
);
