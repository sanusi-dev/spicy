import Swal from 'sweetalert2';

window.Swal = Swal;

const iconMap = {
  success: 'success',
  error: 'error',
  warning: 'warning',
  info: 'info',
};

function showMessage(message, level) {
  Swal.fire({
    toast: true,
    position: 'bottom-end',
    showConfirmButton: false,
    timer: 3500,
    timerProgressBar: true,
    icon: iconMap[level] || undefined,
    titleText: message,
  });
}

function handleMessages(messages) {
  if (!Array.isArray(messages)) {
    return;
  }
  messages.forEach((msg) => showMessage(msg.message, msg.level));
}

const messagesEl = document.getElementById('django-messages');
if (messagesEl) {
  try {
    handleMessages(JSON.parse(messagesEl.textContent));
  } catch (e) {
    // A malformed message must not block the rest of the page JS.
  }
}

document.body.addEventListener('showMessages', (e) => {
  handleMessages(Array.isArray(e.detail) ? e.detail : e.detail?.value);
});
